/**
 * Shared utilities used across all dashboard pages.
 */

/**
 * Escape HTML entities to prevent XSS when injecting into innerHTML.
 */
function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Check the current user's role.
 *
 * Admin nav links are now rendered server-side via Jinja2, so this
 * function no longer toggles visibility.  It is kept as a no-op so
 * existing callers (``await checkUserRole()``) don't break.
 */
async function checkUserRole() {
    // Admin navigation is now handled server-side in base.html.
}

/**
 * Load homegroups into a <select> element.
 * @param {string} selectId - The id of the select element.
 */
async function loadHomegroups(selectId) {
    const r = await fetch('/api/meta/homegroups');
    const j = r.ok ? await r.json() : { homegroups: [] };
    const el = document.getElementById(selectId);
    el.innerHTML = '<option value="">All</option>'
        + (j.homegroups || []).map(h =>
            `<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`
        ).join('');
}

/**
 * Show a temporary toast/alert at the top of the page.
 * @param {string} message
 * @param {string} type - Bootstrap alert type: 'danger', 'success', 'warning', 'info'
 */
function showAlert(message, type = 'danger') {
    const existing = document.getElementById('globalAlert');
    if (existing) existing.remove();

    const icon = type === 'success' ? 'check-circle'
        : type === 'warning' ? 'exclamation-circle'
        : type === 'info' ? 'info-circle'
        : 'exclamation-triangle';

    const div = document.createElement('div');
    div.id = 'globalAlert';
    div.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3`;
    div.style.zIndex = '9999';
    div.style.maxWidth = '600px';
    div.innerHTML = `<i class="fas fa-${icon} me-2"></i>${escapeHtml(message)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    document.body.prepend(div);

    setTimeout(() => { if (div.parentNode) div.remove(); }, 5000);
}

/**
 * Show a temporary error alert (convenience wrapper).
 * @param {string} message
 */
function showError(message) {
    showAlert(message, 'danger');
}

/**
 * Show a temporary success alert (convenience wrapper).
 * @param {string} message
 */
function showSuccess(message) {
    showAlert(message, 'success');
}
