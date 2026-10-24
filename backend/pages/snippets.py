"""Begin's code snippets (spec 4.3): the text each Copy button copies and each block shows. Edit them here, keeping each exactly as it runs.
The three client snippets must equal backend/docs/pages/agent.md (pages.tests_chapters checks), and the commands keep their
spelling (the same tests pin them). A template cannot hold a multi-line value in an include, so chapters/begin.html takes them from here."""

BEGIN_SNIPPETS = {
    "claude_code": 'claude mcp add --transport http veilfile https://veilfile.com/mcp \\\n  --header "Authorization: Bearer $VEILFILE_API_KEY"',
    "codex": "codex mcp add veilfile --url https://veilfile.com/mcp --bearer-token-env-var VEILFILE_API_KEY",
    "cursor": (
        "{\n"
        '  "mcpServers": {\n'
        '    "veilfile": {\n'
        '      "url": "https://veilfile.com/mcp",\n'
        '      "headers": { "Authorization": "Bearer ${env:VEILFILE_API_KEY}" }\n'
        "    }\n"
        "  }\n"
        "}"
    ),
    "command_line": (
        "npm install -g veilfile     # or: pip install veilfile\n"
        "veilfile login              # approve in your browser\n"
        "veilfile push report.pdf    # prints a private link"
    ),
    "http": (
        "curl -X POST https://veilfile.com/api/v1/artifacts \\\n"
        '  -H "Authorization: Bearer $VEILFILE_API_KEY" \\\n'
        '  -F "file=@screenshot.png" -F "ttl_days=7"'
    ),
}
