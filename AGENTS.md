# 开发约定

- 后端代码、依赖、配置、测试和开发文档集中于 `/backend`；前端预留于 `/frontend`。
- 采用 Django + DRF + Uvicorn/ASGI。参考 AiInterviewer 的组织方式，不引入其面试业务。
- API 适配层负责输入校验与响应，业务规则放入各应用的 `services.py`，持久化由 ORM 管理。
- 排错先查实现、契约及数据流，再查配置和运行环境；有证据后再考虑框架问题。
- 在关键状态变化、外部调用和异常分支记录可诊断日志；不得记录密钥、密码或敏感正文。
- 未经用户明确授权，不添加隐式降级、自动重试或修改实验参数、随机种子、数据划分及评估指标。
- 删除旧实现时同步清理调用、配置、注释和测试。
- Markdown 按当前职责、接口、配置、操作和约束组织，不写按日期排列的开发流水、阶段测试次数或本轮提交/服务状态；运行详情保留在结构化报告中，交付检查在会话中说明。

## 注释与检查

- 每个变更代码文件顶部使用 `Responsibilities, Implementation, Related Modules, Declaration Index, Variable Index`；无声明或变量时写 `None`。
- 函数及方法说明 `Functionality, Inputs, Outputs, Logic, Constraints`；类说明 `Functionality, Logic, Constraints`。注释解释设计理由与限制，不编造引用。
- 索引只列本文件实际实现的声明；方法使用可定位的限定名称，不列导入及未覆写的继承方法。
- 实现、注释、索引、测试和说明须同步更新；提交时纳入同一提交。
- 从 `/backend` 执行 `python manage.py check`、`python manage.py makemigrations --check --dry-run`、`python manage.py test` 和 `ruff check .`。
- 当前未引入 `tools/check_docs.py`，交付前人工检查注释与索引；如果后续引入该工具，必须执行其检查，不得绕过失败。
- 交付说明列出实际检查及未验证项，明确是否提交；通过测试不等于实际外部服务验证。
