"use client";

import { useState } from "react";
import { ChevronRight, FileText, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import lessons from "@/lib/lesson-resources.json";
import styles from "./resources.module.css";

export function ResourceReader() {
  const [selected, setSelected] = useState({
    lesson: lessons[0],
    artifact: lessons[0].artifacts[0],
  });
  const [navigationOpen, setNavigationOpen] = useState(true);
  const title = `第 ${selected.lesson.number} 课 · ${selected.lesson.title}`;

  return (
    <main id="main-content" className={styles.reader} data-navigation-open={navigationOpen}>
      <aside className={styles.sidebar} hidden={!navigationOpen}>
        <h1>课程资料</h1>
        <nav aria-label="课程资料树">
          {lessons.map((lesson) => (
            <details key={lesson.number} open className={styles.branch}>
              <summary>
                <ChevronRight size={15} className={styles.chevron} />
                <span>
                  第 {lesson.number} 课<span className={styles.lessonTitle}>{lesson.title}</span>
                </span>
              </summary>
              <ul>
                {lesson.artifacts.map((artifact) => (
                  <li key={artifact.id}>
                    <button
                      type="button"
                      aria-current={selected.artifact.href === artifact.href ? "page" : undefined}
                      onClick={() => {
                        setSelected({ lesson, artifact });
                        if (window.matchMedia("(max-width: 700px)").matches)
                          setNavigationOpen(false);
                      }}
                    >
                      <FileText size={15} />
                      <span>{artifact.title}</span>
                    </button>
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
            onClick={() => setNavigationOpen(!navigationOpen)}
            aria-label={navigationOpen ? "收起目录" : "展开目录"}
            aria-expanded={navigationOpen}
          >
            {navigationOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
          </button>
          <span className={styles.currentTitle}>{title}</span>
          <a href={selected.artifact.href} target="_blank" rel="noreferrer">
            新窗口
          </a>
          <a
            href={selected.artifact.href}
            download={`Compass-L${selected.lesson.number}-${selected.artifact.id}.html`}
          >
            下载
          </a>
        </div>
        <iframe
          key={selected.artifact.href}
          src={selected.artifact.href}
          title={`${title} · ${selected.artifact.title}`}
          className={styles.frame}
        />
      </section>
    </main>
  );
}
