import { expect, test } from "@playwright/test";

const developerToken = process.env.E2E_DEVELOPER_TOKEN || "";

test("developer can clone a ruleset and add a DB catalog entry", async ({ page }) => {
  test.skip(!developerToken, "E2E_DEVELOPER_TOKEN is required.");
  await page.goto("/__developer/catalog");
  await expect(
    page.getByRole("heading", { name: "Catalog Developer" }),
  ).toBeVisible();
  await page.getByLabel("Developer token").fill(developerToken);
  await page.getByRole("button", { name: "Erişimi doğrula" }).click();
  await expect(
    page.getByRole("heading", { name: "Rules Catalog DB" }),
  ).toBeVisible();

  await page.locator(".developer-rulesets button").first().click();
  await expect(page.getByText(/7 kayıt/)).toBeVisible();
  await page.getByLabel("Sürüm ID").fill("srd-5.2.1-e2e.1");
  await page.getByLabel("Görünen ad").fill("SRD E2E 1");
  await page.getByRole("button", { name: "Seçileni klonla" }).click();
  await expect(page.getByText("Draft ruleset oluşturuldu.")).toBeVisible();

  await page.getByRole("button", { name: "Yeni", exact: true }).click();
  await page.getByLabel("Slug").fill("e2e-rope");
  await page.getByLabel("Ad", { exact: true }).fill("E2E Rope");
  await page.getByLabel("SRD sayfa etiketleri").fill("220");
  await page.getByLabel("Kaynak bölümü").fill("Equipment: E2E Rope");
  await page.getByRole("button", { name: "DB'ye kaydet" }).click();
  await expect(
    page.getByText("Katalog kaydı DB'ye kaydedildi."),
  ).toBeVisible();
  await expect(page.getByText("item:e2e-rope")).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty(
    "scrollWidth",
    await page.locator("html").evaluate((element) => element.clientWidth),
  );
});
