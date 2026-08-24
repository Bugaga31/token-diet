"""Tests for doc_guardian — доки, которые сами себя проверяют."""


from token_diet.doc_guardian import extract_blocks, render_report, verify_block, verify_docs


def test_extract_blocks():
    md = "# Title\n```python\nx=1\n```\n```bash\necho hi\n```"
    blocks = extract_blocks(md, file="test.md")
    assert len(blocks) == 2
    assert blocks[0].lang == "python"
    assert blocks[1].lang == "bash"


def test_verify_python_ok():
    from token_diet.doc_guardian import CodeBlock
    b = CodeBlock(lang="python", code="x=1\nprint(x)", file="a.md", line=1)
    verify_block(b)
    assert b.status == "ok"


def test_verify_python_fail():
    from token_diet.doc_guardian import CodeBlock
    b = CodeBlock(lang="python", code="def foo(:\n  pass", file="a.md", line=1)
    verify_block(b)
    assert b.status == "fail"


def test_verify_docs_readme():
    blocks = verify_docs()
    # README + QUICKSTART должны парситься без fail
    fails = [b for b in blocks if b.status == "fail"]
    assert fails == [], f"fails: {fails}"
    report = render_report(blocks)
    assert "DocGuardian" in report
    assert "✅" in report
