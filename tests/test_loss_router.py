"""Tests for loss-tolerance routing and output reduction."""
from app.token_diet.loss_router import compress_tool_output, compress_with_routing, reduce_output


def test_compress_prose_removes_filler():
    """Filler phrases are removed, core content preserved."""
    text = (
        "Furthermore, it is important to note that the stock price is 150. "
        "Moreover, the P/E ratio is 25. "
        "In conclusion, the company is overvalued."
    )
    result, _before, _after = compress_with_routing(text)
    assert _after < _before
    assert "150" in result  # zero-tolerance preserved
    assert "25" in result
    assert "overvalued" in result
    assert "Furthermore" not in result
    assert "Moreover" not in result


def test_compress_preserves_code_blocks():
    """Code blocks are zero-tolerance — never compressed."""
    text = (
        "Here is the code:\n"
        "```python\n"
        "def hello():\n"
        "    print('Hello, World!')\n"
        "```\n"
        "This is a simple function."
    )
    result, _b, _a = compress_with_routing(text)
    assert "def hello():" in result
    assert "print('Hello, World!')" in result


def test_compress_preserves_urls():
    """URLs are zero-tolerance."""
    text = "Check https://example.com/api/v1/data for more info. Furthermore, the data is interesting."
    result, _b, _a = compress_with_routing(text)
    assert "https://example.com/api/v1/data" in result


def test_compress_short_text_unchanged():
    """Short text (<100 chars) is not compressed."""
    text = "Buy SBER"
    result, before, after = compress_with_routing(text)
    assert result == text
    assert before == after


def test_reduce_output_strips_ceremony():
    """Ceremonial phrases are stripped from AI output."""
    text = (
        "Here is your analysis of SBER.\n\n"
        "SBER is trading at 150 RUB with P/E of 25.\n\n"
        "I hope this helps! Let me know if you have questions."
    )
    result = reduce_output(text)
    assert "Here is your analysis" not in result
    assert "I hope this helps" not in result
    assert "Let me know" not in result
    assert "SBER is trading at 150" in result


def test_reduce_output_preserves_code():
    """Code in output is not stripped."""
    text = "Sure! Here is the code:\n```python\nx = 1\n```\nI hope this helps!"
    result = reduce_output(text)
    assert "x = 1" in result
    assert "Sure!" not in result


def test_compress_tool_output_dict():
    """Dict results are compressed via JSON compressor."""
    result = compress_tool_output("get_quote", {
        "data": {
            "ticker": "SBER",
            "price": 150.0,
            "change": 2.5,
            "volume": 1000000,
            "market_cap": 5200000000000,
            "pe_ratio": 25.0,
            "pb_ratio": 1.8,
            "dividend_yield": 12.0,
            "description": "Sberbank of Russia is the largest bank in Russia",
        }
    })
    assert "SBER" in result or "ticker" in result
    assert "150" in result


def test_compress_tool_output_string():
    """String results are compressed via loss-tolerance routing."""
    result = compress_tool_output(
        "search",
        "Furthermore, the SBER stock is trading at 150. It is important to note that P/E is 25.",
    )
    assert "150" in result
    assert "25" in result


def test_compress_tool_output_none():
    """None/empty results return empty string."""
    assert compress_tool_output("test", None) == ""
    assert compress_tool_output("test", "") == ""
    assert compress_tool_output("test", {}) == ""  # empty dict = no info


def test_reduce_output_with_inline_code():
    """Inline code in output is preserved."""
    text = "Use `x = 1` to initialize. Sure!"
    result = reduce_output(text)
    assert "`x = 1`" in result


def test_compress_aggressive_flag():
    """Aggressive flag compresses more aggressively."""
    text = (
        "Furthermore, the stock price is 150. Moreover, the company is overvalued. "
        "Additionally, the P/E ratio is 25. In conclusion, sell the stock."
    )
    _result_norm, _b, a_norm = compress_with_routing(text, aggressive=False)
    _result_agg, _b, a_agg = compress_with_routing(text, aggressive=True)
    # Aggressive should be <= normal size
    assert a_agg <= a_norm
