import { memo } from "react";
import Markdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const markdownComponents: Components = {
  table: ({ children }) => (
    <div className="analysis-markdown-table" tabIndex={0} role="region" aria-label="数据表格">
      <table>{children}</table>
    </div>
  ),
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
};

export const AnalysisMarkdown = memo(function AnalysisMarkdown({
  source,
  revealing = false,
  embedded = false,
}: {
  source: string;
  revealing?: boolean;
  embedded?: boolean;
}) {
  return (
    <div className="analysis-markdown" data-revealing={revealing}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={markdownComponents}
        disallowedElements={embedded ? ["img", "a"] : undefined}
        unwrapDisallowed
        skipHtml
        urlTransform={(url) => defaultUrlTransform(url) || undefined}
      >
        {source}
      </Markdown>
    </div>
  );
});
