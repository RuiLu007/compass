# Compass AI 资产

`hooks/` 提供 Claude Code 与 Codex 共用的[编辑后自动格式化](hooks/README.md)，使用项目已安装的 Prettier / Ruff，仅处理当前编辑事件涉及的文件。配置通过 `.claude/settings.json` 和 `.codex/hooks.json` 软链接加载。

`skills/` 提供 Compass 的项目级 Skill 入口。通过 my-ai-infra 安装的 `my-*` 资产由项目配置维护；UI Skill 随 Compass 仓库保留固定版本。项目自有的 HTML 阅读版 Skill 按用户指定保存在 `skill/`，并从 `skills/` 相对链接发现。

| Skill                                              | 用途                               |
| -------------------------------------------------- | ---------------------------------- |
| [shadcn](skills/shadcn/SKILL.md)                   | shadcn/ui 组件、样式与设计系统     |
| [ui-ux-pro-max](skills/ui-ux-pro-max/SKILL.md)     | UI/UX 检索、配色、排版和技术栈建议 |
| [emil-design-eng](skills/emil-design-eng/SKILL.md) | 界面细节、交互与动画设计           |

三个 UI Skill 的版本、文件校验值和路径适配登记在 [skills-lock.json](skills-lock.json)。许可证和评测随各 Skill 保留。

从 Compass 根目录检查 UI/UX 检索：

```bash
python3 .agents/skills/ui-ux-pro-max/scripts/search.py "keyboard focus modal" --domain ux
```

三个 UI Skill 与 my-ai-infra 的安装清单分别维护。

## 项目自有 Skill

| Skill                                                  | 用途与维护源                                                                                                                                        |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| [my-design-to-html](skills/my-design-to-html/SKILL.md) | 从技术方案生成供人工 review 的离线 HTML；真源位于 [skill/my-design-to-html](skill/my-design-to-html/SKILL.md)，包含内容契约、通用模板、渲染器与评测 |

这个入口由本项目直接维护，不加入 my-ai-infra 的安装清单。用法：`$my-design-to-html 将指定技术方案生成 HTML 阅读版`。模板采用第 13 课已认可的三列目标表、设计图表、关键代码与文件变更清单；生成和检查命令见[生成配置](skill/my-design-to-html/references/generation.md)。
