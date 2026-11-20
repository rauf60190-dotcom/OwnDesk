// Anonymous GET-only checks of the deployed portal. Never log response bodies or cookies.
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const ownRequire = createRequire(path.resolve(__dirname, '../frontend/package.json'));
const { chromium, expect } = ownRequire('@playwright/test');
const origin = 'https://owndesk.work';

(async () => {
  const output = path.resolve(process.env.OWNDESK_SMOKE_OUTPUT || path.join(os.tmpdir(),'OwnDesk-public-smoke-'+Date.now()));
  await fs.mkdir(output, { recursive: false });
  const result = { success:false, target:origin, target_kind:'deployed_portal',
    executor:process.env.GITHUB_ACTIONS === 'true' ? 'github-actions' : 'local',
    tool_commit:process.env.GITHUB_SHA || null, checks:[], blocked_mutations:0,
    blocked_external_requests:0, page_error_types:[], authenticated:false,
    accounts_created:0, emails_requested:0, response_bodies_saved:false, cookies_saved:false,
    not_tested:['new registration','email delivery','Google account','organization installation','employee login'] };
  let browser;
  try {
    result.stage='launch browser';
    browser = await chromium.launch({ headless:true });
    const context = await browser.newContext({ viewport:{width:1280,height:900},
      reducedMotion:'reduce', serviceWorkers:'block' });
    await context.route('**/*', route => {
      const request = route.request();
      if (!['GET','HEAD'].includes(request.method())) { result.blocked_mutations++; return route.abort(); }
      if (new URL(request.url()).origin !== origin) { result.blocked_external_requests++; return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => result.page_error_types.push(error.name));
    result.stage='public health document';
    const live = await page.goto(origin+'/api/v1/health/live', {waitUntil:'domcontentloaded'});
    expect(live.status()).toBe(200);
    async function api(endpoint, denied=false) {
      result.stage = 'api ' + endpoint;
      const response = await page.evaluate(async endpoint => {
        const response = await fetch(endpoint, {method:'GET',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(15000),headers:{Accept:'application/json'}});
        const json = (response.headers.get('content-type') || '').includes('application/json');
        let options = null;
        if (endpoint === '/api/v1/auth/options' && response.status === 200 && json) {
          const value = await response.json();
          options = {deployment_mode:value.deployment_mode,local_email_preview:value.local_email_preview};
        }
        return {status:response.status,json,options};
      }, endpoint);
      const {status,json} = response;
      result.checks.push({ endpoint, status, json, expected:denied ? 'anonymous denial' : 'public JSON' });
      expect(status).toBeGreaterThanOrEqual(200);
      if (denied) {
        expect([401,403]).toContain(status);
        expect(json).toBe(true);
        return;
      }
      expect(status).toBe(200);
      expect(json).toBe(true);
      return response.options;
    }
    await api('/api/v1/health/live');
    const options = await api('/api/v1/auth/options');
    expect(options.deployment_mode).toBe('portal');
    expect(options.local_email_preview).toBe(false);
    for (const endpoint of ['/api/v1/auth/me','/api/v1/auth/security','/api/v1/portal/installations']) await api(endpoint,true);
    result.stage = 'public homepage';
    const home = await page.goto(origin+'/', {waitUntil:'domcontentloaded'});
    expect(home.status()).toBe(200);
    await expect(page.locator('main.product-welcome')).toBeVisible();
    await expect(page.locator('.welcome-hero h1')).toBeVisible();
    await expect(page.locator('input[type=password]')).toHaveCount(0);
    await expect(page.getByRole('link',{name:'Войти',exact:true}).first()).toBeVisible();
    result.homepage_shows_product=true;
    await page.screenshot({path:path.join(output,'public-home.png')});
    result.stage = 'login page';
    await page.getByRole('link',{name:'Войти',exact:true}).first().click();
    await expect(page).toHaveURL(origin+'/login');
    await expect(page.locator('input[type=password]').first()).toBeVisible();
    result.login_page_visible=true;
    result.stage = 'privacy page';
    await page.goto(origin+'/privacy', {waitUntil:'domcontentloaded'});
    await expect(page.getByRole('heading',{level:1,name:'Конфиденциальность в OwnDesk'})).toBeVisible();
    result.privacy_page_visible=true;
    result.stage = 'terms page';
    await page.goto(origin+'/terms', {waitUntil:'domcontentloaded'});
    await expect(page.getByRole('heading',{level:1,name:'Использование OwnDesk'})).toBeVisible();
    result.terms_page_visible=true;
    result.stage = 'mobile homepage';
    await page.setViewportSize({width:390,height:844});
    await page.goto(origin+'/', {waitUntil:'domcontentloaded'});
    await expect(page.locator('.welcome-hero h1')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true);
    await page.screenshot({path:path.join(output,'public-home-mobile.png')});
    result.mobile_homepage_no_overflow=true;
    expect(result.page_error_types).toEqual([]);
    result.success=true;
    result.stage='verified';
  } catch (error) {
    result.error_type=error.name;
    process.exitCode=1;
  } finally {
    if(browser) await browser.close().catch(()=>{});
    await fs.writeFile(path.join(output,'public-smoke.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  }
})().catch(error=>{ console.error(error.name); process.exitCode=1; });
