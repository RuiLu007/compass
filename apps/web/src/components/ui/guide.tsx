"use client";

import { CheckCircle2, CircleAlert, Workflow } from "lucide-react";
import { useId, useRef, useState } from "react";
import { MetricSuggestionsCard, type MetricSuggestion } from "@/features/semantic/result-actions";
import { UiButton } from "./button";
import { UiPageHeading } from "./page-heading";
import { Modal } from "./dialog";
import { UiCodeBlock } from "./code-block";
import { UiDisclosure } from "./disclosure";
import { UiExecutionExample } from "./execution-example";

const colors = [
  ["页面", "--canvas"],
  ["卡片", "--surface"],
  ["正文", "--ink"],
  ["辅助文字", "--muted"],
  ["主要操作", "--primary"],
  ["成功背景", "--primary-soft"],
  ["边界", "--border"],
  ["代码背景", "--code-surface"],
] as const;

const script = `const lead = await agent("channel_compare", args);
const scope = { ...args, channelCode: lead.channelCode, leadEvidenceRef: lead.evidenceRef };

const [products, review] = await parallel([
  () => agent("product_breakdown", scope),
  () => agent("scope_review", scope),
]);

return { status: "ok", evidenceRefs: [lead.evidenceRef, products.evidenceRef, review.evidenceRef] };`;

export function UiGuide() {
  const [sampleSaved, setSampleSaved] = useState<string[]>([]);
  const [sampleHidden, setSampleHidden] = useState<string[]>([]);
  const suggestions: MetricSuggestion[] = [
    {
      id: "retried",
      name: "支付重试挽回率",
      unit: "%",
      summary: "首次支付失败的订单中，24 小时内重试并支付成功的订单占比，同一订单只计一次。",
      formula: "24 小时内重试成功的订单数 ÷ 首次支付失败的订单数 × 100%",
      reason: "按周比较重试流程改版前后的支付恢复情况。",
    },
    {
      id: "elapsed",
      name: "支付恢复时长中位数",
      unit: "分钟",
      summary: "对首次失败后 24 小时内支付成功的订单，衡量从首次失败到首次成功的典型等待时间。",
      formula: "中位数（首次成功时间 − 首次失败时间）",
      reason: "定期评估支付重试流程的等待时间。",
    },
  ];
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [name, setName] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [savedName, setSavedName] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerInput = useRef<HTMLInputElement>(null);
  const invalid = attempted && !name.trim();

  return (
    <div className="ui-guide">
      <header className="ui-guide-header">
        <h1>Compass UI 规范</h1>
        <p>查看组件、操作状态与代码展示。示例与产品页面共用样式和组件。</p>
        <nav aria-label="规范章节">
          <a href="#ui-foundations">基础规范</a>
          <a href="#ui-components">组件演示</a>
          <a href="#ui-code">代码展示</a>
          <a href="#ui-interaction">交互演示</a>
          <a href="#ui-execution">执行过程</a>
          <a href="#ui-semantic">指标保存建议</a>
        </nav>
      </header>

      <section
        className="ui-guide-section"
        id="ui-foundations"
        aria-labelledby="ui-foundations-title"
      >
        <h2 id="ui-foundations-title">基础规范</h2>
        <dl className="ui-guide-colors">
          {colors.map(([label, token]) => (
            <div key={token}>
              <span
                className="ui-guide-swatch"
                style={{ background: `var(${token})` }}
                aria-hidden="true"
              />
              <dt>{label}</dt>
              <dd>
                <code>{token}</code>
              </dd>
            </div>
          ))}
        </dl>
        <div className="ui-guide-grid">
          <section className="ui-panel">
            <h3>文字层次</h3>
            <p className="ui-guide-type-body">正文用来解释指标、结果和下一步。</p>
            <p className="ui-panel-description">控件与表单使用 14 px。标题使用 18 px。</p>
            <small className="ui-field-hint">辅助说明使用 12 px。</small>
          </section>
          <section className="ui-panel">
            <h3>间距与边界</h3>
            <p className="ui-panel-description">
              以 4 px 为间距基准。卡片内边距 20 px，圆角 14 px，控件圆角 8 px。
            </p>
            <p className="ui-panel-description">操作区可以换行。触屏控件至少高 44 px。</p>
          </section>
        </div>
      </section>

      <section
        className="ui-guide-section"
        id="ui-components"
        aria-labelledby="ui-components-title"
      >
        <h2 id="ui-components-title">组件演示</h2>
        <div className="ui-guide-grid">
          <section className="ui-panel ui-guide-example">
            <h3>按钮</h3>
            <div className="ui-actions">
              <UiButton onClick={() => input.current?.focus()}>填写名称</UiButton>
              <UiButton
                variant="secondary"
                onClick={() => {
                  setName("");
                  setAttempted(false);
                  setSavedName("");
                }}
              >
                重置示例
              </UiButton>
              <UiButton busy>保存中</UiButton>
            </div>
          </section>
          <section className="ui-panel ui-guide-example">
            <h3>状态反馈</h3>
            <p className="ui-notice" data-tone="success">
              <CheckCircle2 size={17} aria-hidden="true" /> 工作流已保存
            </p>
            <p className="ui-notice" data-tone="error">
              <CircleAlert size={17} aria-hidden="true" /> 保存失败，请重试
            </p>
          </section>
        </div>
      </section>

      <section className="ui-guide-section" id="ui-execution" aria-labelledby="ui-execution-title">
        <h2 id="ui-execution-title">执行过程</h2>
        <UiExecutionExample />
      </section>

      <section className="ui-guide-section" id="ui-semantic" aria-labelledby="ui-semantic-title">
        <h2 id="ui-semantic-title">指标保存建议</h2>
        <MetricSuggestionsCard
          items={suggestions
            .filter((m) => !sampleHidden.includes(m.id))
            .map((m) => ({ ...m, draftId: sampleSaved.includes(m.id) ? m.id : null }))}
          error=""
          busy={false}
          onDecide={(ids, action) =>
            (action === "save" ? setSampleSaved : setSampleHidden)((prev) => [...prev, ...ids])
          }
          draftUrl={() => "#ui-semantic"}
        />
        {(sampleSaved.length > 0 || sampleHidden.length > 0) && (
          <UiButton
            variant="quiet"
            onClick={() => {
              setSampleSaved([]);
              setSampleHidden([]);
            }}
          >
            重置指标示例
          </UiButton>
        )}
      </section>

      <section className="ui-guide-section" id="ui-code" aria-labelledby="ui-code-title">
        <h2 id="ui-code-title">代码展示</h2>
        <UiCodeBlock title="渠道分析编排" language="JavaScript" code={script} />
        <UiDisclosure label="查看组件用法">
          <UiCodeBlock
            title="组件示例"
            language="TSX"
            code={
              'import { UiCodeBlock } from "@/components/ui/code-block";\n\n<UiCodeBlock title="编排脚本" language="JavaScript" code={scriptSource} />'
            }
          />
        </UiDisclosure>
      </section>

      <section
        className="ui-guide-section"
        id="ui-interaction"
        aria-labelledby="ui-interaction-title"
      >
        <UiPageHeading title="交互演示" titleId="ui-interaction-title">
          <UiButton variant="secondary" onClick={() => setDrawerOpen(true)}>
            在抽屉中编辑
          </UiButton>
        </UiPageHeading>
        <div className="ui-panel ui-guide-example">
          <h3>填写名称并确认保存</h3>
          <form
            className="ui-guide-example"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              setAttempted(true);
              if (name.trim()) dialog.current?.showModal();
              else input.current?.focus();
            }}
          >
            <div className="ui-field">
              <label htmlFor={`${id}-name`}>工作流名称</label>
              <input
                ref={input}
                className="ui-input"
                id={`${id}-name`}
                value={name}
                maxLength={80}
                required
                placeholder="例如：渠道异动归因"
                aria-invalid={invalid || undefined}
                aria-describedby={invalid ? `${id}-error` : undefined}
                onChange={(event) => setName(event.target.value)}
              />
              {invalid && (
                <p id={`${id}-error`} className="ui-notice" data-tone="error" role="alert">
                  请输入工作流名称。
                </p>
              )}
            </div>
            <div className="ui-actions">
              <UiButton type="submit">
                <Workflow size={16} aria-hidden="true" /> 保存工作流
              </UiButton>
            </div>
          </form>
          <div role="status" className="ui-guide-feedback">
            {savedName && (
              <p className="ui-notice" data-tone="success">
                <CheckCircle2 size={17} aria-hidden="true" /> 已确认保存「{savedName}」
              </p>
            )}
          </div>
        </div>
      </section>

      {drawerOpen && (
        <Modal
          open
          placement="right"
          title="编辑工作流名称"
          initialFocus={drawerInput}
          onClose={() => setDrawerOpen(false)}
          footer={
            <>
              <UiButton variant="secondary" onClick={() => setDrawerOpen(false)}>
                取消
              </UiButton>
              <UiButton form={`${id}-drawer`} type="submit">
                应用名称
              </UiButton>
            </>
          }
        >
          <form
            id={`${id}-drawer`}
            onSubmit={(event) => {
              event.preventDefault();
              setName(drawerInput.current!.value.trim());
              setDrawerOpen(false);
            }}
          >
            <label className="ui-field">
              工作流名称
              <input className="ui-input" ref={drawerInput} defaultValue={name} required />
            </label>
          </form>
        </Modal>
      )}
      <dialog ref={dialog} className="ui-panel ui-dialog" aria-labelledby={`${id}-dialog-title`}>
        <h2 id={`${id}-dialog-title`}>保存工作流</h2>
        <p className="ui-panel-description">将「{name.trim()}」保存为可复用的工作流。</p>
        <form method="dialog" className="ui-actions">
          <UiButton type="submit" variant="secondary">
            暂不保存
          </UiButton>
          <UiButton type="submit" onClick={() => setSavedName(name.trim())}>
            确认保存
          </UiButton>
        </form>
      </dialog>
    </div>
  );
}
