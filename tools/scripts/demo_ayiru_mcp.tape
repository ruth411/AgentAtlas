# vhs tape: 60s demo of `ayiru-mcp` install + first cited answer.
#
# To render: `vhs tools/scripts/demo_ayiru_mcp.tape` → `tools/scripts/demo.gif`.
# Requires https://github.com/charmbracelet/vhs installed locally.
#
# Story arc:
#   1. pip install ayiru-mcp
#   2. show the wheel's size (point: small, no fastembed)
#   3. send a tools/list to confirm 6 tools
#   4. send a tools/call ask with a real gh question
#   5. show the cited answer with cli.github.com URL

Output tools/scripts/demo.gif

Set FontSize 14
Set Width 1100
Set Height 700
Set Padding 20
Set TypingSpeed 60ms
Set Theme "Catppuccin Mocha"

Type "# 1. Install (this would normally take ~5s)"
Enter
Type "python -m venv /tmp/ayiru-demo && /tmp/ayiru-demo/bin/pip install -q ayiru-mcp"
Enter
Sleep 4s

Type "# 2. Wheel is small — no fastembed, just pydantic + sqlalchemy + ayiru-core + bundled catalog"
Enter
Type "du -sh /tmp/ayiru-demo/lib/python*/site-packages/ayiru_mcp"
Enter
Sleep 2s

Type "# 3. Six advertised tools"
Enter
Type "printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}\\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}\\n' | /tmp/ayiru-demo/bin/ayiru-mcp | python -c 'import sys, json; [print(\"  -\", t[\"name\"]) for t in json.loads(sys.stdin.readlines()[1])[\"result\"][\"tools\"]]'"
Enter
Sleep 3s

Type "# 4. Ask a real question — get a cited answer from cli.github.com"
Enter
Type "printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}\\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"ask\",\"arguments\":{\"question\":\"how do I open a PR from my current branch?\",\"tool_id_hint\":\"gh\"}}}\\n' | /tmp/ayiru-demo/bin/ayiru-mcp | python -c 'import sys, json; r = json.loads(sys.stdin.readlines()[1])[\"result\"][\"structuredContent\"]; a = r[\"answers\"][0]; print(\"subject:  \", a[\"subject\"]); print(\"citation: \", a[\"evidence\"][0][\"source_uri\"])'"
Enter
Sleep 5s

Type "# That's it. Add to claude_desktop_config.json and your agent has cited gh knowledge."
Enter
Sleep 3s
