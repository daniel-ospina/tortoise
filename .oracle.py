from markdown_it import MarkdownIt
MD = MarkdownIt("commonmark")

def show(body):
    print(repr(body))
    print("   ", MD.render(body).replace("\n", ""))

for b in [
    "- Notes\n2.     Closes #100",
    "  1.\n        x\n    2.      Closes #100",
    "  1.\n        x\n  2.      Closes #100",
    "- Notes\nlabelled",
    "- Notes\n# H\n2.     Closes #100",
    "- Notes\n2. x",
    "- item\ncont",
    "- item\n2. x",
    "- item\n* x",
    "> - item\n2. x",
    "  1.\n        x\n    2.      Closes #100\n",
    "Notes\n1. \n    Closes #100",
    "> Notes\n<span>x</span>\n    ```\n2.     Closes #100",
]:
    show(b)
