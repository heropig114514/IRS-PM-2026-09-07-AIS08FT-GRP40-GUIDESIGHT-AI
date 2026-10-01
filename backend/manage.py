"""Responsibilities: Django 管理命令入口，供迁移、检查、测试和用户管理使用。
Implementation: 默认装配本地设置，再将命令行交给 Django；不捕获启动错误。
Related Modules: config.settings；生产使用 DJANGO_SETTINGS_MODULE=config.production。
Declaration Index: main — 设置默认配置并执行管理命令。
Variable Index: None。
"""

import os
import sys

from django.core.management import execute_from_command_line


def main():
    """Functionality: 执行 Django 管理命令。
    Inputs: sys.argv 及进程环境，包含必填 DJANGO_SECRET_KEY。
    Outputs: 无返回值；命令产生控制台输出，错误保持原异常或退出状态。
    Logic: 仅在环境未指定设置模块时选择开发配置，再委派 Django。
    Constraints: 须在已安装依赖的 Python 环境中运行；不隐式生成密钥。
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
