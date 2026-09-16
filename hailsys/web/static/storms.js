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
    if (button.dataset.days) params.set('days', button.dataset.days);
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