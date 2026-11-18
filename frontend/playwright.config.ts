import { defineConfig, devices } from '@playwright/test';
export default defineConfig({ testDir: '../tests/e2e', workers: 1, use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' }, projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }], reporter: [['list'], ['html', { open: 'never' }]] });
