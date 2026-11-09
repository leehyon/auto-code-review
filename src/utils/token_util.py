import tiktoken

from src.utils.log import logger

# 编码器缓存。tiktoken 首次 get_encoding 会去下载 BPE 文件，
# 拿不到就抛异常。这里做一层保护：编码器不可用时退化成按字符估算，
# 宁可 token 数估得不那么准，也不能让整个审查失败。
_ENCODING_CACHE = {}


def get_encoding(encoding_name: str = "cl100k_base"):
    """取编码器，失败时返回 None（不抛异常）。"""
    if encoding_name in _ENCODING_CACHE:
        return _ENCODING_CACHE[encoding_name]
    try:
        encoding = tiktoken.get_encoding(encoding_name)
    except Exception as e:
        logger.warning(f"tiktoken 编码器 {encoding_name} 不可用（{e}），"
                       f"token 计数退化为按字符估算")
        _ENCODING_CACHE[encoding_name] = None
        return None
    _ENCODING_CACHE[encoding_name] = encoding
    return encoding


def estimate_tokens(text: str) -> int:
    """按字符估算 token 数。

    经验值：英文约 3 字符/token，中文约 1.5 字符/token。
    编码器不可用时的兜底，宁可偏高（早截断）也不要偏低（超预算）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if '一' <= ch <= '鿿')
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 3.0)


def count_tokens(text: str) -> int:
    """
    计算文本的 token 数量。

    Args:
        text (str): 输入文本。

    Returns:
        int: token 数量。
    """
    encoding = get_encoding("cl100k_base")  # 适用于 OpenAI GPT 系列
    if encoding is None:
        return estimate_tokens(text)
    return len(encoding.encode(text))


def truncate_text_by_tokens(text: str, max_tokens: int, encoding_name: str = "cl100k_base") -> str:
    """
    根据最大 token 数量截断文本。

    Args:
        text (str): 需要截断的原始文本。
        max_tokens (int): 最大 token 数量。
        encoding_name (str): 使用的编码器名称，默认为 "cl100k_base"。

    Returns:
        str: 截断后的文本。
    """
    encoding = get_encoding(encoding_name)

    # 编码器不可用时，按估算比例截断字符
    if encoding is None:
        estimated = estimate_tokens(text)
        if estimated <= max_tokens:
            return text
        ratio = max_tokens / max(estimated, 1)
        return text[:max(int(len(text) * ratio), 1)]

    # 将文本编码为 tokens
    tokens = encoding.encode(text)

    # 如果 tokens 数量超过最大限制，则截断
    if len(tokens) > max_tokens:
        truncated_tokens = tokens[:max_tokens]
        truncated_text = encoding.decode(truncated_tokens)
        return truncated_text

    return text


if __name__ == '__main__':
    text = "Hello, world! This is a test text for token counting."
    print(count_tokens(text))  # 输出：11
    print(truncate_text_by_tokens(text, 5))  # 输出："Hello, world!"
