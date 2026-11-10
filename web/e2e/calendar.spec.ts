import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
test('Google Calendar: authorize, link with consent, confirm a booking and disconnect', async ({ page }) => {
  test.setTimeout(90000);
  expect((await page.request.post('/api/backend/auth/login', { data: {
    email: 'calendar@example.com', password: 'appointment-browser-passphrase',
  } })).ok()).toBeTruthy();
  await page.route('https://accounts.google.com/**', route => {
    const state = new URL(route.request().url()).searchParams.get('state');
    return route.fulfill({ status: 302, headers: { location: 'http://localhost:3000/api/backend/google-calendar/callback?state=' + state + '&code=browser-code' } });
  });
  await page.goto('/dashboard/appointments');
  await page.getByText('Google Calendar', { exact: true }).click();
  await expect(page.getByText('No Google accounts connected.')).toBeVisible();
  await page.getByRole('textbox', { name: 'Account label' }).fill('Appointment team');
  const returned = page.waitForResponse(response => response.request().isNavigationRequest()
    && new URL(response.url()).pathname === '/dashboard/appointments');
  await page.getByRole('button', { name: 'Connect Google account', exact: true }).click();
  await returned;
  await page.waitForLoadState('domcontentloaded');
  await expect(page).toHaveURL(/\/dashboard\/appointments$/);
  await page.getByText('Google Calendar', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Appointment team · Connected', exact: true })).toBeVisible();
  const link = page.getByRole('form', { name: 'Link a calendar', exact: true });
  await link.getByRole('combobox', { name: 'Calendar', exact: true }).selectOption({ label: 'Appointment team · Appointment calendar' });
  await link.getByRole('combobox', { name: 'Service', exact: true }).selectOption({ label: 'Calendar consultation' });
  await expect(link.getByRole('button', { name: 'Link calendar', exact: true })).toBeDisabled();
  await link.getByRole('checkbox').check();
  await link.getByRole('button', { name: 'Link calendar', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove calendar link', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reserve this slot' }).first()).toBeVisible();
  // Use the API for synthetic contact entry; no phone number appears in the screenshot.
  const slots = await (await page.request.get('/api/backend/appointments/slots')).json();
  const conversations = await (await page.request.get('/api/backend/conversations?limit=200')).json();
  const held = await (await page.request.post('/api/backend/appointments/bookings', { data: {
    slot_id: slots.find((s: { state: string }) => s.state === 'available').id, conversation_id: conversations[0].id,
    customer_name: 'Demo customer', customer_phone: '+919999000001', language: 'en',
  }, headers: { 'X-CSRF-Token': (await page.context().cookies()).find(c => c.name === 'chatbot_csrf')!.value } })).json();
  const csrf = (await page.context().cookies()).find(c => c.name === 'chatbot_csrf')!.value;
  expect((await page.request.post('/api/backend/appointments/bookings/' + held.id + '/confirm', {
    data: { confirm: true }, headers: { 'X-CSRF-Token': csrf },
  })).ok()).toBeTruthy();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Calendar: Calendar synchronized', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'हिन्दी', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Google खाता जोड़ें', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'English', exact: true }).click();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 320, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../docs/screenshots/t9-9-google-calendar.png', fullPage: true });
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Disconnect account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Appointment team · Disconnected', exact: true })).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Remove calendar link', exact: true }).click();
  await expect(page.getByText('No calendar links. Manual availability is active.')).toBeVisible();
});
