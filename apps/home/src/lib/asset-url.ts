export function assetUrl(path: string) {
  return `${process.env.NEXT_PUBLIC_HOME_BASE_PATH ?? ""}${path}`;
}

export const prototypeUrl = assetUrl("/prototype/index.html#analysis");
