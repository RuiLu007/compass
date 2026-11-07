import type { Page } from "@playwright/test";

// Dispatch browser visibility events deterministically without relying on OS focus in headless CI.
export async function setVisibility(page: Page, state: "hidden" | "visible") {
  await page.evaluate((value) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => value });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
}

export async function observePageFlashes(page: Page) {
  await page.evaluate(() => {
    const root = document.querySelector<HTMLElement>("[data-protected-root]")!;
    const flashes: string[] = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const attribute = record.attributeName!;
        if (record.oldValue !== null || root.hasAttribute(attribute)) flashes.push(attribute);
      }
    });
    observer.observe(root, {
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ["hidden", "inert"],
    });
    Reflect.set(window, "observedProtectedRoot", root);
    Reflect.set(window, "protectedRootFlashes", flashes);
  });
}

export async function pageFlashes(page: Page) {
  return page.evaluate(() => ({
    sameRoot:
      Reflect.get(window, "observedProtectedRoot") ===
      document.querySelector("[data-protected-root]"),
    flashes: Reflect.get(window, "protectedRootFlashes") as string[],
  }));
}
