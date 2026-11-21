"use client";

import { useState, useSyncExternalStore } from "react";
import type { MouseEvent as ReactMouseEvent, SyntheticEvent } from "react";
import { ChevronRight, FileText, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { assetUrl } from "@/lib/asset-url";
import lessons from "@/lib/lesson-resources.json";
import styles from "./resources.module.css";

type LessonArtifact = {
  id: string;
  title: string;
  href: string;
  markdown?: { href: string };
};

const narrowViewport = "(max-width: 700px)";

function subscribeViewport(onChange: () => void) {
  const media = window.matchMedia(narrowViewport);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function resourceHref(lesson: number, doc: string, section?: string) {
  const params = new URLSearchParams({ lesson: String(lesson), doc });
  if (section) params.set("section", section);
  return `?${params}`;
}

function isUnmodifiedClick(event: MouseEvent | ReactMouseEvent<HTMLAnchorElement>) {
  return (
    !event.defaultPrevented &&
    event.button === 0 &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey
  );
}

export function ResourceReader() {
  const params = useSearchParams();
  const selectedLesson = lessons.find((lesson) => String(lesson.number) === params.get("lesson"));
  const lesson = selectedLesson ?? lessons[0];
  const selectedArtifact = selectedLesson?.artifacts.find((item) => item.id === params.get("doc"));
  const activeDocument: LessonArtifact = selectedArtifact ?? lesson.artifacts[0];
  const { markdown } = activeDocument;
  const section = selectedArtifact ? params.get("section") : null;
  const documentUrl = assetUrl(activeDocument.href);
  const frameSrc = section ? `${documentUrl}#${section}` : documentUrl;
  const title = `第 ${lesson.number} 课 · ${lesson.title}`;
  const narrow = useSyncExternalStore(
    subscribeViewport,
    () => window.matchMedia(narrowViewport).matches,
    () => false,
  );
  const [navigationPreference, setNavigationPreference] = useState<boolean>();
  const navigationOpen = navigationPreference ?? !(narrow && selectedLesson);

  function navigate(href: string) {
    if (window.location.search !== href || window.location.hash) {
      window.history.pushState(null, "", href);
    }
    if (window.matchMedia(narrowViewport).matches) setNavigationPreference(false);
  }

  function followDocumentLinks(event: SyntheticEvent<HTMLIFrameElement>) {
    const frame = event.currentTarget;
    frame.contentDocument?.addEventListener("click", (click) => {
      if (!isUnmodifiedClick(click)) return;
      const link = (click.target as Element | null)?.closest?.<HTMLAnchorElement>("a[href]");
      if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self"))
        return;
      if (link.origin !== window.location.origin || link.pathname === documentUrl) return;
      const nextLesson = lessons.find((item) =>
        item.artifacts.some(({ href }) => assetUrl(href) === link.pathname),
      );
      const nextArtifact = nextLesson?.artifacts.find(
        ({ href }) => assetUrl(href) === link.pathname,
      );
      if (!nextLesson || !nextArtifact) return;
      click.preventDefault();
      navigate(resourceHref(nextLesson.number, nextArtifact.id, link.hash.slice(1)));
    });
  }

  return (
    <main id="main-content" className={styles.reader} data-navigation-open={navigationOpen}>
      <aside className={styles.sidebar} hidden={!navigationOpen}>
        <h1>课程资料</h1>
        <nav aria-label="课程资料树">
          {lessons.map((lesson) => (
            <details key={lesson.number} open className={styles.branch}>
              <summary>
                <ChevronRight size={15} className={styles.chevron} />
                <span className={styles.lessonNumber}>第 {lesson.number} 课</span>
                <span className={styles.lessonTitle} title={lesson.title}>
                  {lesson.title}
                </span>
              </summary>
              <ul>
                {lesson.artifacts.map((artifact) => (
                  <li key={artifact.id}>
                    <a
                      href={resourceHref(lesson.number, artifact.id)}
                      aria-current={activeDocument.href === artifact.href ? "page" : undefined}
                      onClick={(event) => {
                        if (!isUnmodifiedClick(event)) return;
                        event.preventDefault();
                        navigate(event.currentTarget.search);
                      }}
                    >
                      <FileText size={15} />
                      <span>{artifact.title}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </nav>
      </aside>
      <section className={styles.document} aria-label={title}>
        <div className={styles.toolbar}>
          <button
            type="button"
            onClick={() => setNavigationPreference(!navigationOpen)}
            aria-label={navigationOpen ? "收起目录" : "展开目录"}
            aria-expanded={navigationOpen}
          >
            {navigationOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
          </button>
          <span className={styles.currentTitle}>{title}</span>
          <a href={frameSrc} target="_blank" rel="noreferrer">
            新窗口
          </a>
          <a href={documentUrl} download={`Compass-L${lesson.number}-${activeDocument.id}.html`}>
            {markdown ? "下载 HTML" : "下载"}
          </a>
          {markdown && (
            <a
              href={assetUrl(markdown.href)}
              download={`Compass-L${lesson.number}-${activeDocument.id}.md`}
            >
              下载 Markdown
            </a>
          )}
        </div>
        <iframe
          key={frameSrc}
          src={frameSrc}
          title={`${title} · ${activeDocument.title}`}
          className={styles.frame}
          onLoad={followDocumentLinks}
        />
      </section>
    </main>
  );
}
