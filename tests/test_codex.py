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


# ---- security: human-verified spellings, symlinks, NotebookEdit, public pushes ----------

GUARD_SH = REPO / 'plugins/open-science-project/scripts/human_verified_guard.sh'
PUSH_GUARD = REPO / 'plugins/open-science-project/scripts/push_guard.py'
sys.path.insert(0, str(REPO / 'plugins/open-science-project/scripts'))
import human_verified  # noqa: E402

# Each sets verification to human-verified in YAML; the old regex missed all but the first.
SPELLINGS = [
    'verification: human-verified',
    '"verification": human-verified',
    "'verification': 'human-verified'",
    'verification: |-\n  human-verified',
    'verification: >-\n  human-verified',
    'verification: !!str human-verified',
    'verification: "human\\x2dverified"',
    'verification: "human\\u002dverified"',
    'verification:\n  human-verified',
    'a: &v human-verified\nverification: *v',
    'node: {verification: human-verified}',
]


def front(body):
    return '---\nid: r1\n' + body + '\nstatus: active\n---\n# R1\n'


def claude_guard(payload):
    return subprocess.run(['bash', str(GUARD_SH)], input=json.dumps(payload), text=True,
                          capture_output=True)


@pytest.mark.parametrize('body', SPELLINGS)
def test_every_yaml_spelling_is_human_verified(body):
    assert yaml.safe_load(front(body).split('---')[1])  # control: the YAML is valid
    found = yaml.safe_load(front(body).split('---')[1])
    found = found.get('verification') or found['node']['verification']
    assert found == 'human-verified'


@pytest.mark.parametrize('use_yaml', [True, False])
@pytest.mark.parametrize('body', SPELLINGS)
def test_human_verified_spellings_are_found_with_and_without_pyyaml(body, use_yaml):
    assert human_verified.adds_human_verified('r.md', front('verification: verified'), front(body), use_yaml)
    assert not human_verified.adds_human_verified('r.md', front(body), front(body), use_yaml)
    # control: a value that only starts with the word is not the setting
    assert not human_verified.adds_human_verified(
        'r.md', '', front('verification: verified\nhuman-verified-note: x'), use_yaml)


@pytest.mark.parametrize('body', SPELLINGS)
def test_claude_guard_refuses_every_spelling(project, body):
    path = str(project / 'tasks/r.md')
    assert claude_guard({'tool_name': 'Write', 'tool_input': {'file_path': path,
                         'content': front(body)}}).returncode == 2
    (project / 'tasks/r.md').write_text(front('verification: verified'))
    r = claude_guard({'tool_name': 'Edit', 'tool_input': {'file_path': path,
                      'old_string': 'verification: verified', 'new_string': body}})
    assert r.returncode == 2 and 'only the user' in r.stderr


@pytest.mark.parametrize('body', SPELLINGS)
def test_codex_guard_refuses_every_spelling(project, body):
    added = '\n'.join('+' + line for line in front(body).splitlines())
    command = '*** Begin Patch\n*** Add File: tasks/r.md\n' + added + '\n*** End Patch\n'
    assert hook(project, command).returncode == 2
    # A block scalar whose value line alone changes, with the key as patch context
    (project / 'tasks/s.md').write_text(front('verification: |-\n  verified'))
    command = ('*** Begin Patch\n*** Update File: tasks/s.md\n@@\n verification: |-\n'
               '-  verified\n+  human-verified\n*** End Patch\n')
    assert hook(project, command).returncode == 2


def test_guard_allows_an_edit_that_keeps_an_existing_setting(project):
    path = project / 'tasks/r.md'
    path.write_text(front('title: a\nverification: human-verified'))
    edit = {'file_path': str(path), 'old_string': 'title: a\nverification: human-verified',
            'new_string': 'title: b\nverification: human-verified'}
    assert claude_guard({'tool_name': 'Edit', 'tool_input': edit}).returncode == 0
    path.write_text(front('title: a\nverification: verified'))  # control
    edit['old_string'] = 'title: a\nverification: verified'
    assert claude_guard({'tool_name': 'Edit', 'tool_input': edit}).returncode == 2


def test_guard_resolves_symlinks_into_a_project(project, tmp_path):
    link = tmp_path / 'elsewhere'
    link.symlink_to(project / 'tasks')
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(link / 'r.md'),
               'content': front('verification: human-verified')}}
    assert claude_guard(payload).returncode == 2
    outside = tmp_path / 'plain'
    outside.mkdir()
    payload['tool_input']['file_path'] = str(outside / 'r.md')  # control: not a project
    assert claude_guard(payload).returncode == 0


def test_guard_checks_notebook_edits(project):
    hooks = json.loads((REPO / 'plugins/open-science-project/hooks/hooks.json').read_text())
    matchers = [h['matcher'] for h in hooks['hooks']['PreToolUse']]
    assert any('NotebookEdit' in m.split('|') for m in matchers)
    payload = {'tool_name': 'NotebookEdit', 'tool_input': {
        'notebook_path': str(project / 'tasks/n.ipynb'), 'new_source': 'verification: human-verified'}}
    assert claude_guard(payload).returncode == 2
    payload['tool_input']['new_source'] = 'verification: verified'
    assert claude_guard(payload).returncode == 0


PUSHES = [
    ('git push public main', True), ('git -C . push --mirror origin', True),
    ('git push public; echo done', True), ('git status && echo push public', False),
    ('git push origin main', False), ('git status', False),
    ('git commit -m "push public later"', False),
    ('opsci publish push --export-id abc --message release', False),
    ('git -C .opsci/public push origin main', True),
    ('git -C ./.opsci/public/ push', True),
    ('cd .opsci/public && git push origin main', True),
    ('cd .opsci && cd public && git push', True),
    ('git --git-dir=.opsci/public/.git push origin main', True),
    ('GIT_DIR=.opsci/public/.git git push origin main', True),
    ('sh -c "git push public main"', True),
    ("bash -lc 'echo; ' && bash -c 'cd .opsci/public; git push'", True),
    ('eval git push public main', True),
    ('git push https://github.com/someone/demo-public.git main', True),
    ('git push https://github.com/someone/demo-private.git main', False),
    ('git -C .opsci/public log -1', False),
]


@pytest.fixture
def pushproj(project):
    man = project / 'publish/manifest.yaml'
    man.write_text('public_repo: https://github.com/someone/demo-public\n' + man.read_text())
    return project


@pytest.mark.parametrize('command,blocked', PUSHES)
def test_public_push_guard_both_agents(pushproj, command, blocked):
    r = hook(pushproj, command, tool='Bash')
    assert (r.returncode == 2) == blocked, r.stderr
    payload = {'tool_name': 'Bash', 'cwd': str(pushproj), 'tool_input': {'command': command}}
    c = subprocess.run(['python3', str(PUSH_GUARD)], input=json.dumps(payload), text=True,
                       capture_output=True)
    assert (c.returncode == 2) == blocked, c.stderr
    if blocked:
        assert c.stderr == r.stderr  # the same message for both agents (R03)


def test_public_push_guard_in_the_public_checkout(pushproj):
    co = pushproj / '.opsci/public'
    co.mkdir(parents=True)
    payload = {'tool_name': 'Bash', 'cwd': str(co), 'tool_input': {'command': 'git push origin main'}}
    for cmd in (['python3', str(PUSH_GUARD)], [sys.executable, str(SCRIPT), 'pre']):
        assert subprocess.run(cmd, input=json.dumps(payload), text=True, capture_output=True).returncode == 2
    payload['cwd'] = str(pushproj)  # control
    assert subprocess.run(['python3', str(PUSH_GUARD)], input=json.dumps(payload), text=True,
                          capture_output=True).returncode == 0


def test_claude_hooks_run_the_push_guard_on_bash():
    hooks = json.loads((REPO / 'plugins/open-science-project/hooks/hooks.json').read_text())
    bash = [h for h in hooks['hooks']['PreToolUse'] if h['matcher'] == 'Bash']
    assert bash and 'push_guard.py' in bash[0]['hooks'][0]['command']
