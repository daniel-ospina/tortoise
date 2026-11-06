from markdown_it import MarkdownIt
MD = MarkdownIt("commonmark")

def show(body):
    print(f"{body!r:60s} -> {MD.render(body).replace(chr(10), '')}")

for lead in ("- Notes", "  1.\n        x", "> - item"):
    for ind in (0, 1, 2, 3, 4, 5, 6):
        for marker in ("2.", "-", "1.", "*"):
            body = f"{lead}\n{' ' * ind}{marker}      Closes #100"
            show(body)
    print()
