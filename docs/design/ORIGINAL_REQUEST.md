# Original request (verbatim, 2026-09-23)

The user's first message, which started the design. Later decisions in
`REPORT_framework_design.md` and `BUILD_PLAN.md` take precedence where they differ.

---

I want to make a new framework for doing science from now on. This is a framework that can be used for Human, AI, or Human + AI hybrid driven scientific work.

The goal is to have a framework so that people can share their work openly, where everything is reproducible and verifiable, and no effort has to be wasted by other people who want to do the same thing. Also it would include all of the failed routes so that people know what has failed.

The concrete goal is so that I will have a “wiki” like page on my github pages personal website, where I list all the projects that I am working on, are on going, etc, with the title and a brief description. This needs its own template and can be hosted in a repo (or maybe everything I say here should be in the same repo).

Then, For each project, I want to have a template that has (i) a framework for project development (in the spirit of my new project, new plan, new context and context management skill), (ii) a framework for regularly compiling the repo into a public repo, through a filter, (iii) publishing most documents in the repo as html on a github page, (iv) publishing associated data that is needed for the project on zenodo.

for (i), it is in the spirit of my new project, new plan, new context and context management skills. However some aspects I want it to be different from our current skills.

For the project: the new project skill should just be something like “follow the template in [path] to build a new project from the template”, and if there is no template locally yet, then just clone the template on github that we will make.

Structure of project: there has to be (a) some claude.md or agents.md, (b) same as now, a main agent contract, (c) a subagent contract, (d) a project map, (e) a citation file, (f) the current project level context, (g) some kind of “diary” or “log” of the development progress on the project level, (h) task level subdirs with task level current context and task level diary or log, as well as a task level map. (i) a rules file

(a) can be similar as the current one, but as this would be a template, make sure to leave things more flexible (absolutely no absolute paths or anything that pertains only to me or my projects.

(b) and (c) same agent dispatch rules can be kept

(d) the project map is a high level map. I am thinking whether it makes more sense to make this a directory or just some md. basically it is the place to go to to understand the whole logic of the project (basically it should be some kind of graph that connects different pieces), e.g. what is superceded by what, what depends on what, what is related to what. It should not contain all details but should point to the per task maps. It should be well maintained and updated, and should NOT be bloated. If things get out of hand perhaps there can be multiple files, so this can actually be a directory.

(e) this is a collection of works that we have actively used in the project. Maybe this should be a directory, and should contain works that we consulted but didn’t really use, and a stronger list of works that we actively used, including any packages. this list should be updated and well maintained.

(f) this is a file where an agent can read and immediately start picking up the project and continue pushing. It contains the minimal information (other things should be offloaded to per-task context or the log), e.g. “now tasks A B C are complete, now we are working on task D”, etc. It should be kept under 200 lines and maintained well. It should be edited in place, if something is superseded it should be edited directly. It should only contain the bare minimum that allows an agent to read and immediately continue from here.

(g) the log: this should be a longer ledger / log, that is complete and is not edited in place like the context. Almost like a diary. But this could become super long at some point, so we need to be as brief as possible, and multiple files can be used so agents don’t need to read so many things (unless maybe now frontier models know how to read a snippet of a file only so context won’t be an issue?) This should be appended and updated well.

(h) the per task working directory. It should have a main context document, plan file (if applicable), subcontext documents in a sub-context directory (just like now), and other working files here.

(i) a project level rules file that agents can consult. Rules should be edited in place and removed if they are obsolete. Do not make agents read this automatically, but only point to the relevant rules in the different context files. This can be a directory and new files can be made to explain / detail some of the rules if there are too many rules and / or one rule is too long, so that the context won’t blow up.

(j) the data should be stored somewhere (either in the working dir or a data dir within the project, which should be symlinked to scratch if the data is humungous). Perhaps it shouldn’t be in the git repo.

And then, a “filter” that converts this private repo into a separate, public one. The filter should screen (a) all things that would violate security, e.g. private keys, very sensitive personal or security information, (b) anything embarassing, e.g. offensive words or statements, disrespectful statements about other people’s work, unfriendly judgment “XXX’s work is wrong” etc, (c) there should be a file in the private repo (of course, don’t put it in the public repo) that states what shouldn’t be put in the public repo, (d) check that all things are well cited, and that unverified or ongoing results or tasks should be flagged as such, before pulling those into the public repo (I think better do some manual transfer instead of git pulling). These checks can simply be done only to the git diff things that have changed in the private repo.

And then, a project level github page (maybe hosted as a branch in the same public repo that hosts the project?), that has some tabs for results (like the silencio project we have, the website where we publish the results), and some tabs for human readable project map, citation, current context, work level context etc, no need to be polished, just so that everything in the git repo is readable on the public website.

Finally, a template of a page in a personal github pages (with some kind of minimal style, specifically make sure the agent has this sentence in prompts for front end design: “Do not use a cream or off-white background, italic accent words in headlines, numbered "01/02/03" section labels, monospace labels, or pill-shaped buttons.” And it lists the projects, with some kind of human description (encourage the human to write this one their own), and each project can have some arbitrary tags, and on the website one can screen or rank by the tags.


Some notes:

0. as we will be making some new “new plan”, “context management” skills etc, change the name of the skills we have now to “new plan old” and “context management old” etc so they won’t be overwritten.
1. git should be allowed without approval.
2. whenever an agent finishes a subtask (even small ones), i.e. when they are editing the task level context documents, they should immediately think whether the task level map or the project level context or the project level map or the citation has to be edited, so that those are very up to date.
3. I don’t want to use the planning or spec or brainstorming superpowers anymore, so don’t include these in the skill. Just make a generic new plan skill
4. also I think we should include a skill for people to migrate their projects into this framework.
5. Most projects will have some kind of core source code that is shared between tasks. Then, the source code should be in some src directory instead of the working directory of different tasks, and each task should use a different worktree to avoid conflict. If agents or humans work in the working directory to test things, they should always think of whether to put those things into the src dir when they are done.
