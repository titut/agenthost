# Final response rules

- When the user asks for a Markdown file, use the `write_markdown` tool.
- After the tool returns a `FILE_PATH:` line, you MUST reply with a short confirmation that includes the exact path.
- Example: "Done. I saved the file to: output/markdown_writer/filename.md"
- If the user is asking a follow-up question or did not request a file, answer normally and do not call the tool again.
