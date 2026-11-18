import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }, testInfo) => {
  await page.goto('/');
  await page.getByLabel('Логин', { exact: true }).fill(testInfo.title.startsWith('menu persists') ? 'demo-operator' : 'admin');
  await page.getByLabel('Пароль', { exact: true }).fill(process.env.E2E_PASSWORD || 'Local-review-only-2026');
  await page.getByRole('button', { name: 'Войти в пространство' }).click();
  await expect(page.getByRole('heading', { name: 'Активные', exact: true })).toBeVisible();
});

test('menu persists, free queue and operator flow', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.getByRole('button', { name: 'Запросы', exact: true }).click();
  await expect(page.getByRole('link', { name: /^Свободные/ })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('link', { name: /^Свободные/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Запросы', exact: true }).click();
  await page.getByRole('link', { name: /^Свободные/ }).click();
  await expect(page.getByRole('heading', { name: 'Свободные', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Новое обращение', exact: true }).click();
  await page.getByLabel('Тема', { exact: true }).fill('E2E: проверка основного пути');
  await page.getByRole('button', { name: 'Создать обращение', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'E2E: проверка основного пути', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await expect(page.locator('.properties-pane').getByText('В работе', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Внутренняя заметка', exact: true }).click();
  await page.getByLabel('Текст внутренней заметки').fill('Проверка заметки: русский, O‘zbekcha, English.');
  await page.getByRole('button', { name: 'Добавить заметку' }).click();
  await expect(page.locator('.timeline').getByText('Проверка заметки: русский, O‘zbekcha, English.')).toBeVisible();
  await page.getByLabel('Изменить статус').selectOption('resolved');
  await page.getByLabel('Краткий результат').fill('Сценарий проверен автоматически.');
  await page.getByRole('button', { name: 'Отметить решённым' }).click();
  await expect(page.locator('.properties-pane').getByText('Решено', { exact: true })).toBeVisible();
  await page.getByLabel('Изменить статус').selectOption('closed');
  await expect(page.locator('.properties-pane').getByText('Закрыто', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('responsive queue and theme', async ({ page }) => {
  await page.screenshot({ path: '../docs/screenshots/queue-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Переключить тему' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: 'Переключить тему' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'Меню', exact: true })).toBeVisible();
  await expect.poll(() => page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(width.scroll).toBeLessThanOrEqual(width.viewport);
  await page.screenshot({ path: '../docs/screenshots/queue-mobile.png', fullPage: true, animations: 'disabled' });
});
