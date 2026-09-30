import assert from 'node:assert/strict';
import test from 'node:test';
import { archiveSidebarBatch } from './archive_sidebar_ui.mjs';

const keep = '00000000-0000-0000-0000-000000000001';
const old = '00000000-0000-0000-0000-000000000002';
const plan = { schema_version: 1, confirmed_unwanted_sidebar_entries: true,
  keep_through_id: keep, protected_ids: [keep], selected_ids: [old],
  matches: [{ thread_id: old, title: 'Old' }] };
const labels = { navigationLabel: 'Home', actionsLabel: 'Actions', archiveLabel: 'Archive' };

function browser({ boundary = true, title = 'Old' } = {}) {
  let removed = false, menu = false, clicks = 0;
  const rows = () => [...(boundary ? [{ title: 'Keep', href: `/c/${keep}` }] : []),
    ...(removed ? [] : [{ title, href: `/c/${old}` }])];
  const groups = { evaluateAll: async () => rows(), filter: () => ({
    getByRole: () => ({ press: async () => { menu = true; } }),
  }) };
  const tab = { playwright: {
    domSnapshot: async () => menu ? 'menuitem "Archive"' : rows().map(row => row.href).join('\n'),
    locator: value => value,
    getByRole: role => role === 'navigation' ? { getByRole: () => groups } : {
      click: async () => { removed = true; menu = false; clicks++; },
    },
  } };
  return { tab, clicks: () => clicks };
}

test('archives a reviewed ID, records progress and leaves server verification pending', async () => {
  const mock = browser(), progress = [];
  const result = await archiveSidebarBatch(mock.tab, plan, { ...labels, onProgress: row => progress.push(row) });
  assert.equal(mock.clicks(), 1);
  assert.equal(progress[0].id, old);
  assert.equal(result.completed.length, 1);
  assert.equal(result.archive_listing_verified, false);
  assert.equal(result.permanent_deletions, 0);
});

test('missing boundary or changed exact title prevents archive', async () => {
  for (const options of [{ boundary: false }, { title: 'A valid different conversation' }]) {
    const mock = browser(options);
    await assert.rejects(archiveSidebarBatch(mock.tab, plan, labels));
    assert.equal(mock.clicks(), 0);
  }
});

test('protected IDs and a delete action cannot be selected', async () => {
  for (const [review, options] of [[{ ...plan, selected_ids: [keep] }, labels],
    [plan, { ...labels, archiveLabel: 'Delete' }]]) {
    const mock = browser();
    await assert.rejects(archiveSidebarBatch(mock.tab, review, options));
    assert.equal(mock.clicks(), 0);
  }
});
