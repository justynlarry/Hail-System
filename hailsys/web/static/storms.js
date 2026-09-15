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
    const params = new URLSearchParams({
        date: button.dataset.date,
        type: button.dataset.type,
        submitted: '1',
    });

    if (button.dataset.actionable === '1') {
        params.set('actionable', '1');
    }
    fetch('/storms/zips?' + params)
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