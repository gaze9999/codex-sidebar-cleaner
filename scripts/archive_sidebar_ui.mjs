// Run inside the signed-in Codex browser tool with its existing tab binding.
// Archive exact reviewed IDs through the sidebar when loading conversation contents fails.
// Supply labels observed in the current UI; this helper never opens or deletes a chat.
export async function archiveSidebarBatch(tab, plan, {
  navigationLabel, actionsLabel, archiveLabel, limit = 20, onProgress,
} = {}) {
  const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
  if (plan?.schema_version !== 1 || plan.confirmed_unwanted_sidebar_entries !== true
      || !uuid.test(plan.keep_through_id) || !Array.isArray(plan.selected_ids)
      || !plan.selected_ids.length || plan.selected_ids.some(id => typeof id !== 'string' || !uuid.test(id))
      || new Set(plan.selected_ids).size !== plan.selected_ids.length || !Array.isArray(plan.matches)
      || !Array.isArray(plan.protected_ids) || !plan.protected_ids.includes(plan.keep_through_id))
    throw new Error('An exact reviewed plan with a protected keep boundary is required.');
  if (!navigationLabel || !actionsLabel || !['Archive', '封存'].includes(archiveLabel)
      || !Number.isInteger(limit) || limit < 1 || limit > 20)
    throw new Error('Use observed sidebar labels and an archive action; batch limit is 1-20.');
  const selected = new Set(plan.selected_ids), protectedIds = new Set(plan.protected_ids);
  if (plan.selected_ids.some(id => protectedIds.has(id)))
    throw new Error('Selected IDs overlap the protected conversations.');
  const expected = new Map();
  for (const item of plan.matches) {
    if (!uuid.test(item?.thread_id) || typeof item.title !== 'string' || expected.has(item.thread_id))
      throw new Error('Reviewed matching metadata must have distinct IDs and exact titles.');
    expected.set(item.thread_id, item.title);
  }
  if (plan.selected_ids.some(id => !expected.has(id)))
    throw new Error('Selected IDs require reviewed titles.');
  const completed = [];
  const navigation = tab.playwright.getByRole('navigation', { name: navigationLabel, exact: true });
  const groups = navigation.getByRole('group');
  for (let index = 0; index < limit; index++) {
    await tab.playwright.domSnapshot();
    const rows = await groups.evaluateAll(elements => elements.flatMap(element => {
      const link = element.querySelector('a[href]');
      return link ? [{ title: link.innerText.trim(), href: link.getAttribute('href') }] : [];
    }));
    const route = /^\/(?:g\/g-p-[0-9a-f]{32}\/)?c\/([0-9a-f-]{36})$/;
    const boundary = rows.findIndex(row => route.exec(row.href)?.[1] === plan.keep_through_id);
    if (boundary < 0) throw new Error('Keep boundary is absent; nothing else will be archived.');
    const item = rows.slice(boundary + 1).find(row => selected.has(route.exec(row.href)?.[1]));
    if (!item) break; // Pagination can still be loading; this does not prove completion.
    const id = route.exec(item.href)[1];
    if (item.title !== expected.get(id)) throw new Error(`Title changed for ${id}; review again.`);
    const group = groups.filter({ has: tab.playwright.locator(`a[href=${JSON.stringify(item.href)}]`) });
    await group.getByRole('button', { name: actionsLabel, exact: true }).press('Enter');
    const menu = await tab.playwright.domSnapshot();
    if (!menu.includes(`menuitem ${JSON.stringify(archiveLabel)}`))
      throw new Error('Archive menu did not open; stopped before changing the conversation.');
    await tab.playwright.getByRole('menuitem', { name: archiveLabel, exact: true }).click();
    const after = await tab.playwright.domSnapshot();
    if (after.includes(`/c/${id}`)) throw new Error(`Sidebar removal was not observed for ${id}.`);
    const result = { id, title: item.title, status: 'sidebar_removed_pending_server_verification' };
    completed.push(result);
    if (onProgress) await onProgress(result);
  }
  return { completed, permanent_deletions: 0, archive_listing_verified: false,
    limitation: 'Reload and verify the native archive listing; absence from a partially loaded sidebar is insufficient.' };
}
