// =========================================================
//  管理ページの画面制御
//  2秒ごとに /api/status を取得して画面を更新する
// =========================================================

const STATUS_LABEL = {
    running: 'Running',
    starting: 'Starting',
    stopped: 'Stopped',
    exited: 'Error',
}

let noticeTimer = null

// ---------- 共通処理 ----------

function $(id) {
    return document.getElementById(id)
}

// 要素を作る(ユーザ名などは textContent で入れるため HTML として解釈されない)
function el(tag, props = {}, children = []) {
    const node = document.createElement(tag)
    Object.assign(node, props)
    for (const child of [].concat(children)) {
        if (child !== null && child !== undefined) node.append(child)
    }
    return node
}

async function api(method, url, body) {
    const options = { method, headers: {} }
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json'
        options.body = JSON.stringify(body)
    }
    const res = await fetch(url, options)
    let data = {}
    try {
        data = await res.json()
    } catch (e) {
        // JSON以外が返ってきた場合は空のまま
    }
    if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`)
    return data
}

function notify(message, isError = false) {
    const box = $('notice')
    box.textContent = message
    box.className = 'notice show' + (isError ? ' error' : '')
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (box.className = 'notice'), 4000)
}

// ボタンを押している間は二重押しできないようにする
async function run(button, task) {
    button.disabled = true
    try {
        const data = await task()
        if (data && data.message) notify(data.message)
    } catch (e) {
        notify(e.message, true)
    } finally {
        button.disabled = false
        refresh()
    }
}

function formatUptime(sec) {
    if (sec === null || sec === undefined) return '-'
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = sec % 60
    return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`
}

function shortAddress(address) {
    if (!address || address.length <= 14) return address || ''
    return `${address.slice(0, 7)}…${address.slice(-5)}`
}

function badge(status) {
    return el('span', { className: `badge is-${status}`, textContent: STATUS_LABEL[status] || status })
}

// ---------- 描画 ----------

function renderNode(node, chain, pool) {
    const status = $('node_status')
    status.className = `badge is-${node.status}`
    status.textContent = STATUS_LABEL[node.status] || node.status

    const running = node.status === 'running'
    $('node_blocks').textContent = chain ? chain.length : '-'
    $('node_pool').textContent = pool ? pool.length : '-'
    $('node_uptime').textContent = formatUptime(node.uptime)

    const toggle = $('node_toggle')
    const alive = node.status === 'running' || node.status === 'starting'
    toggle.textContent = alive ? 'Stop' : 'Start'
    toggle.dataset.action = alive ? 'stop' : 'start'

    $('mine_button').disabled = !running
    const auto = $('auto_mine_button')
    auto.disabled = !running || node.auto_mining
    auto.textContent = node.auto_mining ? 'Auto Mining…' : 'Auto Mine'
    $('node_links').classList.toggle('hidden', !running)
}

function renderUsers(users) {
    $('user_count').textContent = users.length
    $('user_empty').classList.toggle('hidden', users.length > 0)

    const rows = users.map((user) => {
        const open = el('a', {
            className: 'btn btn-small',
            // ウォレット画面でユーザ名を表示できるよう、URLに名前を付けて開く
            href: `${user.url}?name=${encodeURIComponent(user.name)}`,
            target: '_blank',
            rel: 'noopener',
            textContent: 'Open ↗',
        })
        if (user.status !== 'running') open.classList.add('disabled')

        const restart = el('button', { className: 'btn btn-small', textContent: 'Restart' })
        restart.onclick = () => run(restart, () => api('POST', `/api/users/${user.port}/restart`))

        const remove = el('button', { className: 'btn btn-small btn-danger', textContent: 'Delete' })
        remove.onclick = () => run(remove, () => api('DELETE', `/api/users/${user.port}`))

        return el('tr', {}, [
            el('td', { className: 'name', textContent: user.name }),
            el('td', { className: 'mono', textContent: user.port }),
            el('td', {}, badge(user.status)),
            el('td', {}, el('div', { className: 'row-actions' }, [open, restart, remove])),
        ])
    })
    $('user_rows').replaceChildren(...rows)
}

function txRow(tx) {
    const isReward = tx.sender_blockchain_address === 'THE BLOCKCHAIN'
    return el('div', { className: 'tx' + (isReward ? ' reward' : '') }, [
        el('span', {
            className: 'tx-from',
            textContent: isReward ? 'MINING REWARD' : shortAddress(tx.sender_blockchain_address),
            title: tx.sender_blockchain_address,
        }),
        el('span', { className: 'tx-arrow', textContent: '→' }),
        el('span', {
            className: 'tx-to',
            textContent: shortAddress(tx.recipient_blockchain_address),
            title: tx.recipient_blockchain_address,
        }),
        el('span', { className: 'tx-value', textContent: Number(tx.value).toFixed(2) }),
    ])
}

function renderLedger(chain, pool) {
    const poolList = $('pool_list')
    if (!pool) {
        poolList.replaceChildren(el('p', { className: 'empty', textContent: 'ノードが停止しています' }))
    } else if (pool.length === 0) {
        poolList.replaceChildren(el('p', { className: 'empty', textContent: '未承認の取引はありません' }))
    } else {
        poolList.replaceChildren(...pool.map(txRow))
    }

    const chainList = $('chain_list')
    if (!chain) {
        chainList.replaceChildren()
        return
    }
    const blocks = chain
        .map((block, index) => ({ block, index }))
        .reverse()
        .map(({ block, index }) => {
            const time = new Date(block.timestamp * 1000).toLocaleTimeString('ja-JP')
            const txs = block.transactions.length
                ? block.transactions.map(txRow)
                : [el('p', { className: 'empty', textContent: index === 0 ? 'Genesis Block' : '取引なし' })]
            return el('div', { className: 'block' }, [
                el('div', { className: 'block-head' }, [
                    el('span', { className: 'block-no', textContent: `#${String(index).padStart(3, '0')}` }),
                    el('span', { textContent: time }),
                    el('span', { textContent: `nonce ${block.nonce}` }),
                    el('span', {
                        className: 'hash',
                        textContent: `prev