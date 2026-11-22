const notices: Record<string, string> = {
  restricted_run_result:
    "本轮未完成，当前账号无法读取本轮使用的数据和执行详情。请联系工作区管理员核对来源数据的读取权限，再重新分析。",
  database_access_denied:
    "当前账号无法读取本轮查询使用的业务数据库数据。请联系工作区管理员，申请对应数据连接和业务范围的只读权限，再重新分析。",
  metric_access_denied:
    "当前账号无法读取本轮使用的指标口径或查询结果。请联系工作区管理员，申请该指标的读取和查询权限，再重新分析。",
  document_access_denied:
    "当前账号无法读取本轮引用的知识文档。请联系工作区管理员申请文档阅读权限，再重新分析。",
  source_access_denied:
    "当前账号无法读取本轮分析使用的来源数据。请联系工作区管理员核对并申请相应的数据读取权限，再重新分析。",
  source_result_expired: "上次查询的临时结果已失效，请重新运行分析以获取最新数据。",
  source_authorization_unavailable: "数据访问权限暂时无法核验，请稍后重试。",
  context_access_changed:
    "当前工作区可使用的数据或工具已变更，请重新分析。如需继续使用原范围，请联系工作区管理员核对权限。",
};

export function sourceAccessNotice(code: string | undefined | null) {
  return code && Object.hasOwn(notices, code) ? notices[code] : undefined;
}
