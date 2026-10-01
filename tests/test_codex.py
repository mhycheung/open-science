"""Claude Code and Codex share records without sharing runtime assumptions."""
import json
import subprocess
import sys
import tomllib

import pytest
import yaml

from conftest import REPO, TEMPLATE, git
from opsci import publish, template
from opsci.notion import setup
from test_onboard import run_secret, ZENODO_TOKEN


SCRIPT = REPO / 'plugins/open-science-project/scripts/codex_hook.py'


@pytest.fixture
def project(tmp_path):
    return template.instantiate(tmp_path / 'project', 'demo', 'Demo', 'User', template=TEMPLATE)


def hook(project, patch, phase='pre', tool='apply_patch'):
    payload = {'cwd': str(project), 'tool_name': tool, 'tool_input': {'command': patch}}
    return subprocess.run([sys.executable, str(SCRIPT), phase], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=project)


def patch(path, added):
    return '*** Begin Patch\n*** Update File: ' + path + '\n@@\n' + added + '\n*** End Patch\n'


def test_codex_human_verification_is_blocked_but_removal_is_allowed(project):
    assert hook(project, patch('context.md', '+verification: human-verified')).returncode == 2
    assert hook(project, patch('context.md', '-verification: human-verified\n+verification: verified')).returncode == 0
    assert hook(project, patch('context.md', '+verification: verified')).returncode == 0


def test_multi_file_patch_checks_all_files_and_move_destination(project):
    command = ('*** Begin Patch\n*** Update File: context.md\n@@\n+okay\n'
               '*** Add File: tasks/t01/result.md\n+verification: "human-verified"\n*** End Patch\n')
    assert hook(project, command).returncode == 2
    command = ('*** Begin Patch\n*** Update File: ../outside.md\n*** Move to: tasks/t01/new.md\n'
               '@@\n+verification: human-verified\n*** End Patch\n')
    assert hook(project, command).returncode == 2
    assert hook(project, patch('../outside.md', '+verification: human-verified')).returncode == 0
    assert hook(project, patch('../outside.md', '+ordinary note'), 'post').stdout == ''


def test_codex_context_caps_after_patch(project):
    (project / 'context.md').write_text('state\n' * 201)
    assert hook(project, patch('context.md', '+state'), 'post').returncode == 2
    (project / 'context.md').write_text('state\n' * 200)
    assert hook(project, patch('context.md', '+state'), 'post').returncode == 0
    (project / 'map/README.md').write_text('map\n' * 151)
    command = patch('context.md', '+state').replace('*** End Patch',
        '*** Update File: map/README.md\n@@\n+map\n*** End Patch')
    assert hook(project, command, 'post').returncode == 2


@pytest.mark.parametrize('command,blocked', [
    ('git push public main', True), ('git -C . push --mirror origin', True),
    ('git push public; echo done', True), ('git status && echo push public', False),
    ('git push origin main', False), ('git status', False),
    ('opsci publish push --export-id abc --message release', False),
])
def test_codex_accidental_push_guard(project, command, blocked):
    assert (hook(project, command, tool='Bash').returncode == 2) == blocked


def test_both_hook_payloads_keep_their_contract(project):
    # Control: the original Claude guard remains active on its original payload.
    payload = {'tool_input': {'file_path': str(project / 'context.md'),
                             'new_string': 'verification: human-verified'}}
    script = REPO / 'plugins/open-science-project/scripts/human_verified_guard.sh'
    r = subprocess.run(['bash', str(script)], input=json.dumps(payload), text=True, capture_output=True)
    assert r.returncode == 2
    assert hook(project, patch('context.md', '+verification: human-verified')).returncode == 2


def test_codex_sandbox_cannot_run_human_credentials_entry(tmp_path):
    result, directory = run_secret(tmp_path, 'zenodo', ZENODO_TOKEN, CODEX_THREAD_ID='test-thread')
    assert result.returncode == 1 and 'own terminal' in result.stderr
    assert not directory.exists()


@pytest.mark.parametrize('trailer', ['Claude-Session: abc', 'Codex-Session: abc',
    'Agent: claude', 'Agent: codex', 'Co-Authored-By: Codex <agent@example.invalid>'])
def test_publish_blame_recognizes_both_agents(project, trailer):
    git(project, 'init', '-q')
    git(project, 'config', 'user.name', 'Test User')
    git(project, 'config', 'user.email', 'test@example.invalid')
    file = project / 'verification.txt'
    file.write_text('verification: verified\n')
    git(project, 'add', '.')
    git(project, 'commit', '-qm', 'Initial human commit')
    assert publish.agent_commit(project, 'HEAD', 'verification.txt', 1) is None
    file.write_text('verification: human-verified\n')
    git(project, 'add', '.')
    git(project, 'commit', '-qm', 'Agent edit\n\n' + trailer)
    assert publish.agent_commit(project, 'HEAD', 'verification.txt', 1) is not None


def test_notion_hook_install_preserves_other_hooks_and_claude_permissions(project):
    original = json.loads((project / '.claude/settings.json').read_text())
    codex = project / '.codex/hooks.json'
    codex.parent.mkdir(exist_ok=True)
    codex.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'keep-me'}]}]}}))
    assert setup.add_sync_hooks(project)
    before = codex.read_text()
    assert not setup.add_sync_hooks(project)
    assert codex.read_text() == before
    assert 'keep-me' in before and setup.HOOK_COMMAND in before
    assert json.loads((project / '.claude/settings.json').read_text())['permissions'] == original['permissions']


def test_notion_template_installs_both_hooks_only_when_enabled(tmp_path):
    on = template.instantiate(tmp_path / 'on', 'on', 'On', 'User', template=TEMPLATE, notion=True)
    off = template.instantiate(tmp_path / 'off', 'off', 'Off', 'User', template=TEMPLATE)
    assert setup.HOOK_COMMAND in (on / '.codex/hooks.json').read_text()
    assert not (off / '.codex/hooks.json').exists()


def test_codex_agent_definitions_are_valid_and_keep_role_contracts(project):
    roles = list((project / '.codex/agents').glob('*.toml'))
    assert {p.stem for p in roles} == {'low-effort', 'med-effort', 'high-effort', 'literature', 'text'}
    for p in roles:
        data = tomllib.loads(p.read_text())
        assert data['name'] == p.stem
        assert data['model_reasoning_effort'] in ('low', 'medium', 'high')
        assert data['developer_instructions']
        assert 'model' not in data  # no Claude model alias passed to Codex
        claude = (project / '.claude/agents' / (p.stem + '.md')).read_text().split('---', 2)
        metadata = yaml.safe_load(claude[1])
        assert data['description'] == metadata['description']
        assert data['model_reasoning_effort'] == metadata['effort']
        assert data['developer_instructions'].strip() == claude[2].strip()


def test_codex_manifests_select_separate_runtime_hooks():
    plugins = [p for root in ('plugins', 'extras') for p in (REPO / root).iterdir()
               if (p / '.claude-plugin/plugin.json').is_file()]
    assert len(plugins) == 5
    for plugin in plugins:
        path = plugin / '.codex-plugin/plugin.json'
        assert path.is_file()
        codex = json.loads(path.read_text())
        claude = json.loads((plugin / '.claude-plugin/plugin.json').read_text())
        assert codex['name'] == claude['name'] and codex['version'] == claude['version']
        assert (plugin / codex['skills']).is_dir()
        if isinstance(codex['hooks'], str):
            hooks = json.loads((plugin / codex['hooks']).read_text())
            assert 'hooks' in hooks
