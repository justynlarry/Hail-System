document.addEventListener('click', function (event) {
    const button = event.target.closest('button.expand')
    if (!button) return;

    const row = button.closest('tr');
    const detail = row.nextElementSibling;
    const cell = detail.querySelector('td');

    if (detail.hidden === false) {
        detail.hidden = true;
        button.textContent = '+';
        return;
    }

    detail.hidden = false;
    button.textContent = '\u2212';

    if (cell.dataset.loaded) return;

    cell.textContent = 'Loading\u2026';
    const params = new URLSearchParams({ submitted: '1' });
    if (button.dataset.date) params.set('date', button.dataset.date);
    if (button.dataset.type) params.set('type', button.dataset.type);
    if (button.dataset.area) params.set('area_name', button.dataset.area);
    if (button.dataset.start) params.set('start', button.dataset.start);
    if (button.dataset.end) params.set('end', button.dataset.end);
    if (button.dataset.actionable === '1') params.set('actionable', '1');

    fetch(button.dataset.endpoint + '?' + params)
    .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
    })
    .then(function (html) {
        cell.innerHTML = html;
        cell.dataset.loaded = '1';
    })
    .catch(function (err) {
        cell.textContent = 'Could not load zips: ' + err.message;
    });

});

// Pulls run in a background thread, so a page that renders "Pulling..."
// would otherwise show it until someone refreshes. Poll only the Status
// cells that are running: the server renders those with data-poll="1"
// (_status_cell.html), and asks /storms/state for the same cell again.
const POLL_MS = 3000;
const MAX_POLLS = 40;               // 40 x 3s = 2 minutes, then stop asking

// Kept here, not on the cell: replacing the cell with the server's fresh
// markup throws away every attribute on it, a counter included, so a
// counter stored there would restart at 1 on every poll and never reach
// the cap.
const pollCounts = new Map();
const pollsInFlight = new Set();    // one request per row at a time

function pollKey(cell) {
    return cell.dataset.date + '|' + (cell.dataset.type || '');
}

function pollRunningPulls() {
    const cells = document.querySelectorAll('td[data-poll="1"]');
    if (!cells.length) {
        clearInterval(pollTimer);   // nothing running any more
        return;
    }
    if (document.hidden) return;    // don't spend requests on a background tab

    cells.forEach(function (cell) {
        const key = pollKey(cell);
        if (pollsInFlight.has(key)) return;

        const tries = (pollCounts.get(key) || 0) + 1;
        pollCounts.set(key, tries);
        if (tries > MAX_POLLS) {
            // A pull stuck at 'running' stops being polled instead of
            // hitting the server for as long as the tab stays open.
            cell.removeAttribute('data-poll');
            return;
        }

        // submitted=1 tells the server the filter form was used, so a
        // missing actionable means "off" and not "the default, on".
        const params = new URLSearchParams({
            submitted: '1',
            date: cell.dataset.date,
            type: cell.dataset.type || '',
        });
        if (cell.dataset.actionable === '1') params.set('actionable', '1');

        pollsInFlight.add(key);
        fetch('/storms/state?' + params)
            .then(function (response) {
                // A signed-out session is redirected to the login page,
                // which fetch follows and reports as a 200. Don't put that
                // page inside a table cell.
                if (response.redirected) {
                    cell.removeAttribute('data-poll');
                    throw new Error('signed out');
                }
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.text();
            })
            .then(function (html) {
                // The reply is the same cell template. Once the pull has
                // finished it has no data-poll, and polling for this row
                // stops without the browser deciding what "finished" means.
                if (cell.isConnected) cell.outerHTML = html;
            })
            .catch(function (err) {
                // Not fatal: try again on the next tick, within the cap.
                // Logged so a broken endpoint is visible in the console
                // rather than looking like a pull that never finishes.
                console.warn('status poll failed for ' + key + ': ' + err.message);
            })
            .finally(function () {
                pollsInFlight.delete(key);
            });
    });
}

const pollTimer = document.querySelector('td[data-poll="1"]')
    ? setInterval(pollRunningPulls, POLL_MS)
    : null;
