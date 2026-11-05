from .db import now

# All product names, incidents, policies and records in this file are fictional demo data.
DOCUMENTS = [
    ("import-v1", "订单导入指南 · v1.0", "import", "1.0",
     "# v1.0 CSV 订单导入\n订单导入支持 UTF-8 编码 CSV 文件，必填列为 order_no、sku、quantity。单次最多 2000 行。\n\n"
     "## E2013：列名不匹配\n在 v1.0 中 E2013 表示 CSV 缺少 order_no 列。保持英文下划线列名，不要改为订单编号。请从 v1.0 页面重新下载模板后上传。此说明仅适用 v1.0。"),
    ("import-v2", "订单导入指南 · v2.0", "import", "2.0",
     "# v2.0 订单导入规范\n订单导入支持 UTF-8 CSV，必填列为 external_order_id、sku、quantity，单次最多 5000 行。\n\n"
     "## E2013：旧模板字段\n升级后导入失败并提示 E2013，表示仍在使用 v1.0 模板的 order_no 字段。"
     "下载 v2.0 模板，将 order_no 映射为 external_order_id，保留订单号原值，再上传。先核对失败任务的 accepted_rows，只有 accepted_rows=0 才能直接重新导入；已有成功行请先按 external_order_id 核对，避免重复订单。"),
    ("import-encoding", "导入编码与重复订单排查", "import", "all",
     "# CSV 乱码与编码\n出现乱码或 E2001 时，使用 UTF-8 编码重新保存 CSV，不要使用 GBK。保持 sku 和订单号为文本，避免前导零丢失。\n\n"
     "## E2009：重复订单\nexternal_order_id 已存在时会提示 E2009。先查询导入任务的成功行和失败行，不要整批重复导入。只修正失败行，保留唯一的 external_order_id。"),
    ("export-v1", "报表导出说明 · v1.0", "export", "1.0",
     "# v1.0 报表导出\n单次报表导出最多 10000 行，使用同步导出。提示 E3002 表示导出行数超过限制，可按日期拆分，每份不超过 10000 行。v1.0 不提供后台异步导出任务。"),
    ("export-v2", "报表导出与超时排查 · v2.0", "export", "2.0",
     "# v2.0 报表导出\n单次最多导出 50000 行，超过 10000 行会进入后台异步任务。页面暂时没有下载链接不代表任务失败，应使用任务编号查询状态。\n\n"
     "## E3008：导出超时\n错误码 E3008 表示后台任务执行超时。先查询任务详情和已知问题；若任务已经 failed，可缩小日期范围后创建新任务，避免反复点击同一个任务。若多个客户同时遇到相同超时，可由客服关联到同一个已知问题跟进。"),
    ("permission-v1", "角色权限说明 · v1.0", "permission", "1.0",
     "# v1.0 员工角色\n管理员可管理成员和导出报表，操作员仅可查看和导入订单。E4030 表示当前角色无导出权限。请联系本组织管理员确认角色，客服不可直接替客户修改权限。"),
    ("permission-v2", "角色与权限变更 · v2.0", "permission", "2.0",
     "# v2.0 角色权限\n管理员可以管理成员、查看订单和导出报表；分析员有 report.export 权限；操作员只有订单查看和导入权限。\n\n"
     "## E4030：导出权限不足\n提示 E4030 或看不到导出按钮时，查询当前角色和 report.export 权限，联系本组织管理员授权分析员或管理员角色，重新登录刷新权限。客服与 AI 不得直接提升用户权限，不能承诺已授权。"),
    ("release-v2", "v2.0 升级与兼容性说明", "general", "2.0",
     "# v2.0 发布说明\n订单导入模板将 order_no 更名为 external_order_id，旧模板不能直接使用。导出新增后台异步任务，分析员角色新增 report.export 权限。\n\n"
     "## 升级检查清单\n升级前导出模板样例，升级后先用少量订单验证。E2013 请参考 v2.0 导入指南；导出长时间排队请查询任务状态；权限问题请组织管理员检查角色。"),
    ("support-process", "售后问题升级与关闭规范", "general", "all",
     "# 技术升级材料\n技术工单至少包含产品版本、问题现象、任务编号（如有）、错误信息、复现步骤、已经尝试的操作以及引用依据。区分客户描述、工具确认事实和待验证推测。\n\n"
     "## 问题关联与关闭\n相似描述只能作为关联候选，必须由客服确认后关联。技术人员修复后将工单置为待客户确认，客户确认后才解决；客户反馈仍未解决可重新打开。\n\n"
     "## 知识回流\n仅从已解决工单创建知识草稿，审核后发布。案例知识保留原客户作用域，不能直接公开给其他客户。"),
    ("export-queue", "导出任务排队与下载失效", "export", "all",
     "# 导出任务排队\n任务状态 queued 表示等待处理，running 表示执行中，completed 表示已生成文件，failed 表示失败。不要把排队当作失败。\n\n"
     "## 下载链接过期\n导出文件下载链接在生成后 24 小时过期。链接提示 E3011 时应重新生成报表，不需要更改账号密码；不要把包含客户数据的下载链接写入公开知识库。"),
    ("import-quantity", "订单数量字段校验", "import", "all",
     "# E2018：数量格式错误\n导入订单 quantity 必须是正整数，不能带单位、逗号或小数。提示数量非法时检查 CSV 对应行的 quantity，修正失败行后再提交。\n\n"
     "## 样例\nquantity=3 是合法值；quantity=3件、quantity=0 或 quantity=1.5 均不合法。不要让模型直接改写并提交客户原始文件。"),
    ("account-scope", "组织与客户数据隔离说明", "permission", "all",
     "# 客户组织隔离\n每个客户组织只能查询自己的订单任务、售后工单与案例知识。任务不存在或属于其他组织时统一返回不可访问，不泄露其他客户名称。\n\n"
     "## 客服处理\n客服在选定客户范围内调查，所有写入记录操作者和时间。客户身份来自认证凭证，不相信对话或模型声称的组织身份。"),
]


def seed(db):
    with db.transaction() as con:
        con.executemany("INSERT OR IGNORE INTO customers VALUES (?,?,?,?)", [
            ("acme", "青禾商贸", "2.0", "专业版"), ("beta", "远山书店", "1.0", "基础版")])
        con.executemany("INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?)", [
            ("IMP-1001", "acme", "import", "failed", "E2013", '{"accepted_rows":0,"failed_rows":36,"template":"v1.0","message":"unknown column: order_no"}'),
            ("EXP-1002", "acme", "export", "failed", "E3008", '{"rows":42000,"timeout_seconds":120,"message":"worker timeout","problem_hint":"PRB-001"}'),
            ("AUTH-1003", "acme", "permission", "failed", "E4030", '{"role":"operator","permission":"report.export","granted":false}'),
            ("IMP-2001", "beta", "import", "failed", "E2013", '{"accepted_rows":0,"failed_rows":8,"message":"missing order_no"}'),
            ("EXP-2002", "beta", "export", "completed", None, '{"rows":800,"message":"completed"}'),
        ])
        for doc_id, title, module, version, content in DOCUMENTS:
            con.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?)",
                        (doc_id, title, module, version, content, None, now()))
        con.execute("INSERT OR IGNORE INTO problems (id,title,module,version,description,status,created_at) VALUES (?,?,?,?,?,?,?)",
                    ("PRB-001", "v2.0 大批量导出执行超时", "export", "2.0", "大于 30000 行的报表在部分场景可能触发 E3008，正在调查。临时建议缩小日期范围。", "investigating", now()))
