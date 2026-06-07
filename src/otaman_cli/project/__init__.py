"""`otaman project` command group (otaman-project-command spec).

Subcommands:
    add        — create remote repo, clone, register, init (CVS-dependent)
    assign     — register an existing local git repo
    list       — show registered repos with status filter
    show       — full detail for one repo
    update     — modify repos[] entry fields
    disable    — mark inactive (excluded from default list)
    enable     — restore to active
    remove     — deregister; optionally delete remote (CVS-dependent)

All CVS operations delegate to `otaman_core.git_host`; no provider logic
lives in `otaman-cli`.
"""
