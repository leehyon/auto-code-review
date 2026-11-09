import os

from dotenv import load_dotenv

from src.llm.factory import Factory
from src.utils.log import logger

# 指定环境变量文件路径
ENV_FILE_PATH = "config/.env"
load_dotenv(ENV_FILE_PATH)


REQUIRED_ENV_VARS = [
    "LLM_PROVIDER",
]

# 允许的 LLM 供应商
LLM_PROVIDERS = {"openai", "deepseek", "qwen", "github_copilot"}

# 每种供应商必须配置的键
LLM_REQUIRED_KEYS = {
    "openai": ["OPENAI_API_KEY", "OPENAI_API_MODEL"],
    "deepseek": ["DEEPSEEK_API_KEY", "DEEPSEEK_API_MODEL"],
    "qwen": ["QWEN_API_KEY", "QWEN_API_MODEL"],
    "github_copilot": ["GITHUB_COPILOT_TOKEN"],
}


def check_env_vars():
    """检查环境变量"""
    missing_vars = [var for var in REQUIRED_ENV_VARS if var not in os.environ]
    if missing_vars:
        logger.warning(f"缺少环境变量: {', '.join(missing_vars)}")
    else:
        logger.info("所有必要的环境变量均已设置。")


def check_llm_provider():
    """检查 LLM 供应商的配置"""
    llm_provider = os.getenv("LLM_PROVIDER")

    if not llm_provider:
        logger.error("LLM_PROVIDER 未设置！")
        return

    if llm_provider not in LLM_PROVIDERS:
        logger.error(f"LLM_PROVIDER 值错误，应为 {LLM_PROVIDERS} 之一。")
        return

    required_keys = LLM_REQUIRED_KEYS.get(llm_provider, [])
    missing_keys = [key for key in required_keys if not os.getenv(key)]

    if missing_keys:
        logger.error(f"当前 LLM 供应商为 {llm_provider}，但缺少必要的环境变量: {', '.join(missing_keys)}")
    else:
        logger.info(f"LLM 供应商 {llm_provider} 的配置项已设置。")

def check_llm_connectivity():
    client = Factory().getClient()
    logger.info(f"正在检查 LLM 供应商的连接...")
    if client.ping():
        logger.info("LLM 可以连接成功。")
    else:
        logger.error("LLM连接可能有问题，请检查配置项。")

def check_config(check_connectivity: bool = True):
    """主检查入口

    :param check_connectivity: 是否做 LLM 连通性检查。这会发真实网络请求，
        网络不通时可能耗时 1~2 分钟（GitHub Copilot 要先探测 endpoint、
        再换 token，各自带 30 秒超时）。Web 服务启动时应该传 False，
        把连通性检查放到后台线程，避免端口迟迟不监听。
    """
    logger.info("开始检查配置项...")
    check_env_vars()
    check_llm_provider()
    if check_connectivity:
        check_llm_connectivity()
    logger.info("配置项检查完成。")