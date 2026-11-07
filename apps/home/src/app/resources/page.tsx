import type { Metadata } from "next";
import { ResourceReader } from "./resource-reader";

export const metadata: Metadata = {
  title: "课程资料",
  description: "按课号浏览 Compass 技术方案，选择左侧资料直接阅读。",
};

export default function ResourcesPage() {
  return <ResourceReader />;
}
