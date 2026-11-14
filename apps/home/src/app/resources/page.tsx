import type { Metadata } from "next";
import { Suspense } from "react";
import { ResourceReader } from "./resource-reader";
import styles from "./resources.module.css";

export const metadata: Metadata = {
  title: "课程资料",
  description: "按课号浏览 Compass 技术方案，选择左侧资料直接阅读。",
};

export default function ResourcesPage() {
  return (
    <Suspense fallback={<main id="main-content" className={styles.reader} aria-busy="true" />}>
      <ResourceReader />
    </Suspense>
  );
}
