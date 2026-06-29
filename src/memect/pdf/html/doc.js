
if (false) {
    const data = {
        // 总页数
        total: 7,
        // 当前页码，1 为第一页
        currentNumber: 1,
        // css 变量 --scale
        scale: 1,
        // 背景颜色（hex）
        bgColor: '#ffffff',
    }
}


function initPage(page) {
    if (page.dataset.init === 'done') return
    page.dataset.init = 'done'
    const spans = page.querySelectorAll('.span')
    const widths = Array.from(spans).map(el => ({
        el,
        dataWidth: parseFloat(el.dataset.width || 0),
        //zoom改变了，这个保持不必，因为默认都是scale=1计算的值，也就是相对dataWidth，所以不需要*zoom
        scrollWidth: el.scrollWidth,
    }))
    widths.forEach(({ el, dataWidth, scrollWidth }) => {
        const s = el.textContent
        if(el.dataset.wingdings=='true'){
            return
        }
        //if (['•', '●','■'].includes(s)) {
            //return
        //}
        const scale = Math.round(dataWidth / scrollWidth * 1000) / 1000
        el.style.transformOrigin = 'left center'
        el.style.transform = `scaleX(${scale})`
    })

    //
    //page.querySelectorAll('.markdown').forEach(fitFontSize)
    page.querySelectorAll('.body > .text').forEach(fitFontSize)
    //在表格中的text，使用另外的算法
    page.querySelectorAll('.body td > .text').forEach(fitTableSize)
    //.text中可能包含inline公式，还可以渲染，但是需要使用引入第三方的库
    //el.innerHTML=renderLatex(el.textContent)

    if (true) {
        page.querySelectorAll('.body >.text').forEach((el) => {
            el.innerHTML = convertInlineMath(el.textContent)
        })
    }

    //先批量生成公式
    const formulas = Array.from(page.querySelectorAll('.formula')).map(el => ({
        el,
        html: renderFormula(el.dataset.latex)
    }))
    formulas.forEach(({ el, html }) => {
        if (html) {
            el.innerHTML = html
        } else {
            //也可以<img src="">
            const img = document.createElement('img')
            img.src = el.dataset.image
            el.appendChild(img)
        }
    })

}

function renderFormula(latex) {
    const useLatex = false
    if (window.katex && latex && useLatex) {
        return window.katex.renderToString(latex, {
            displayMode: true,
            // 错误处理：false=不抛出，渲染为红色文本（推荐）
            throwOnError: false,
            // 错误文本颜色（throwOnError:false 时生效）
            errorColor: '#cc0000',
        })
    } else {
        return ''
    }
}

function fitFontSize(el) {
    el.style.lineHeight = '1.5';
    const maxHeight = el.offsetHeight; // 只读一次
    let lo = 1, hi = maxHeight;
    while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        el.style.fontSize = mid + 'px';
        //当字体很小的时候，没有溢出,el.scrollHeight==maxHeight
        //也就是d==0，可能为字体很小
        const d = el.scrollHeight - maxHeight
        if (d > 0 && d <= 2) {
            lo = mid
            break
        }

        if (d <= 0) {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    el.style.fontSize = lo + 'px';
}
function fitTableSize(el) {
    //算法不一样，从最大字体开始，如果能够塞进去，就不再减少，而
    el.style.lineHeight = '1.5';
    const maxHeight = el.offsetHeight; // 只读一次

    //字体最大为10？
    let lo = 1, hi = Math.min(10, maxHeight);
    while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        el.style.fontSize = mid + 'px';
        const d = el.scrollHeight - maxHeight
        if (d > 0 && d <= 2) {
            lo = mid
            break
        }
        if (d <= 0) {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    el.style.fontSize = lo + 'px';
}
function convertInlineMath(text) {
    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
    // 先按 $...$ 分割，公式部分用 KaTeX，其余部分转义
    const parts = text.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g);
    return parts.map(part => {
        if (part.startsWith('$$')) {
            // 块级公式
            const formula = part.slice(2, -2);
            return katex.renderToString(formula, { throwOnError: false, displayMode: true });
        } else if (part.startsWith('$')) {
            // 行内公式
            const formula = part.slice(1, -1);
            return katex.renderToString(formula, { throwOnError: false, displayMode: false });
        } else {
            // 普通文本，转义
            return escapeHtml(part);
        }
    }).join('');
}
// dom 引用
const pageInput = document.getElementById('page-input')
const pageTotal = document.getElementById('page-total')
const scaleInput = document.getElementById('scale-input')
const bgInput = document.getElementById('bg-input')
const layoutInput = document.getElementById('layout-input')
const showPageNumberInput = document.getElementById('show-page-number-input')
const showPageSectionInput = document.getElementById('show-page-section-input')
const showTableColorInput = document.getElementById('show-table-color-input')
const treeFilterInput = document.getElementById('tree-filter-input')
const viewer = document.querySelector('.viewer')
let tree = null

// 程序化滚动期间，屏蔽 observer 对 currentNumber 的反向更新
let _suppressObserver = false
let _suppressTimer = null
function _suppressObserverFor(ms = 800) {
    _suppressObserver = true
    clearTimeout(_suppressTimer)
    _suppressTimer = setTimeout(() => { _suppressObserver = false }, ms)
}

function applyCurrentNumber(n) {
    const el = document.querySelector(`.page[data-number="${n}"]`)
    if (el) {
        _suppressObserverFor()
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
}

function applyScale(s) {
    document.querySelector('.doc').style.setProperty('zoom', s)
}

function applyBgColor(c) {
    viewer.style.setProperty('--bg-color', c)
}

// data => view
function syncToView() {
    pageInput.value = data.currentNumber
    pageInput.max = data.total
    pageTotal.textContent = data.total
    scaleInput.value = data.scale
    bgInput.value = data.bgColor
    applyScale(data.scale)
    applyBgColor(data.bgColor)
}

// view => data
function bindInputs() {
    pageInput.addEventListener('change', () => {
        let n = parseInt(pageInput.value, 10)
        if (isNaN(n)) n = data.currentNumber
        n = Math.max(1, Math.min(n, data.total))
        pageInput.value = n
        if (n !== data.currentNumber) {
            data.currentNumber = n
            applyCurrentNumber(n)
        }
    })

    scaleInput.addEventListener('input', () => {
        const s = parseFloat(scaleInput.value)
        if (!isNaN(s) && s > 0) {
            data.scale = s
            applyScale(s)
        }
    })

    bgInput.addEventListener('input', () => {
        data.bgColor = bgInput.value
        applyBgColor(bgInput.value)
    })

    const doc = document.querySelector('.doc')
    layoutInput.addEventListener('change', () => {
        if (layoutInput.checked) doc.dataset.showLayout = ''
        else delete doc.dataset.showLayout
    })

    showPageNumberInput.addEventListener('change', () => {
        if (showPageNumberInput.checked) doc.dataset.showPageNumber = ''
        else delete doc.dataset.showPageNumber
    })

    showPageSectionInput.addEventListener('change', () => {
        if (showPageSectionInput.checked) doc.dataset.showPageSection = ''
        else delete doc.dataset.showPageSection
    })

    treeFilterInput.addEventListener('change', () => {
        //console.log('filter', treeFilterInput.value)
        const value = treeFilterInput.value
        for (const el of document.querySelectorAll('.tree-node')) {
            const type = el.dataset.type
            if (['xtext', 'xtable', 'xfigure', 'xformula'].includes(type)) {
                if (value == 'all' || type==value) {
                    el.style.display = ''
                }else {
                    el.style.display = 'none'
                }
            }

        }
    })

    showTableColorInput.addEventListener('change',()=>{
        const showBgColor = showTableColorInput.checked
        //获得所有的表格，设置单元格的颜色，如果有的
            for(const table of document.querySelector('.doc').querySelectorAll('.body table')){
                for(const td of table.querySelectorAll('td')){
                    let color = td.dataset['color']||'#ffffff'
                    if(!showBgColor){
                        color=''
                    }else{
                        
                    }
                    td.style.backgroundColor=color
                }
            }
    })
}

function initEvents() {
    const pages = document.querySelectorAll('.page')
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) initPage(entry.target)
        })
        if (_suppressObserver) return
        // 选择在滚动容器中可见高度最大的页作为当前页
        const viewerRect = viewer.getBoundingClientRect()
        let topPage = null, maxVisible = 0
        pages.forEach(el => {
            const rect = el.getBoundingClientRect()
            const visible = Math.min(rect.bottom, viewerRect.bottom) - Math.max(rect.top, viewerRect.top)
            if (visible > maxVisible) {
                maxVisible = visible
                topPage = el
            }
        })
        if (topPage) {
            const n = parseInt(topPage.dataset.number || '0', 10)
            if (n > 0 && n !== data.currentNumber) {
                data.currentNumber = n
                pageInput.value = n
            }
        }
    }, { root: viewer })
    pages.forEach(el => observer.observe(el))



    document.addEventListener('dblclick', (ev) => {
        const formulaEl = ev.target.closest('.formula')
        if (formulaEl) {
            const dlg = document.getElementById('dlg');
            dlg.innerHTML = window.katex.renderToString(formulaEl.dataset.latex, {})
            dlg.showModal()
        }
    })

    const dlg = document.getElementById('dlg');
    dlg.addEventListener('click', (e) => {
        // dialog 本身是遮罩层，点击内容区不会触发
        if (e.target === dlg) {
            dlg.close();
        }
    });

    document.addEventListener('click',(ev)=>{
        const el = ev.target.closest('[data-xid]')
        if (!el || !tree) return
        tree.expand(el.dataset.xid)
    })
}

function initSplitter() {
    const splitter = document.getElementById('splitter')
    const outline = document.querySelector('.outline')
    if (!splitter || !outline) return

    let startX, startW

    splitter.addEventListener('mousedown', (e) => {
        startX = e.clientX
        startW = outline.getBoundingClientRect().width
        splitter.classList.add('dragging')
        document.body.style.cursor = 'col-resize'
        document.body.style.userSelect = 'none'

        function onMove(e) {
            const w = Math.max(0, startW + e.clientX - startX)
            outline.style.width = w + 'px'
        }
        function onUp() {
            splitter.classList.remove('dragging')
            document.body.style.cursor = ''
            document.body.style.userSelect = ''
            document.removeEventListener('mousemove', onMove)
            document.removeEventListener('mouseup', onUp)
        }
        document.addEventListener('mousemove', onMove)
        document.addEventListener('mouseup', onUp)
    })
}

class Tree extends EventTarget {
    constructor(container, data, options = {}) {
        super()
        this._container = typeof container === 'string'
            ? document.querySelector(container)
            : container
        this._data = data
        this._showRoot = options.showRoot !== false
        this._defaultExpanded = options.defaultExpanded !== false
        this._activeNode = null
        this._render()
    }

    _render() {
        this._container.innerHTML = ''
        const ul = document.createElement('ul')
        ul.className = 'tree'
        const rootNode = this._data && this._data.root ? this._data.root : this._data
        if (this._showRoot) {
            ul.appendChild(this._renderNode(rootNode))
        } else if (rootNode && rootNode.children) {
            for (const child of rootNode.children) {
                ul.appendChild(this._renderNode(child))
            }
        }
        this._container.appendChild(ul)
    }

    _renderNode(node) {
        const li = document.createElement('li')
        const hasChildren = Array.isArray(node.children) && node.children.length > 0
        li.className = 'tree-node' + (hasChildren ? '' : ' leaf')
        li.dataset.type = node.type
        li.dataset.nodeId=node.xid
        li._treeNode = node
        if (this._defaultExpanded && hasChildren) li.classList.add('expanded')

        const row = document.createElement('div')
        row.className = 'tree-node-row'

        const toggle = document.createElement('span')
        toggle.className = 'tree-toggle'
        toggle.addEventListener('click', (e) => {
            e.stopPropagation()
            if (hasChildren) li.classList.toggle('expanded')
        })

        const label = document.createElement('span')
        label.className = 'tree-label'
        label.textContent = node.text != null ? node.text : ''

        row.appendChild(toggle)
        row.appendChild(label)
        row.addEventListener('click', () => {
            this._setActive(li)
            this.dispatchEvent(new CustomEvent('click', { detail: { node } }))
        })

        li.appendChild(row)

        if (hasChildren) {
            const ul = document.createElement('ul')
            for (const child of node.children) {
                ul.appendChild(this._renderNode(child))
            }
            li.appendChild(ul)
        }
        return li
    }

    _setActive(li) {
        if (this._activeNode) this._activeNode.classList.remove('active')
        this._activeNode = li
        li.classList.add('active')
        this.dispatchEvent(new CustomEvent('active', { detail: { node: li._treeNode, el: li } }))
    }

    expand(id){
        const escapedId = CSS.escape(String(id))
        const el = this._container.querySelector(`.tree-node[data-node-id="${escapedId}"]`)
        if (!el) return false

        el.classList.add('expanded')

        let parent = el.parentElement?.closest('.tree-node')
        while (parent) {
            parent.classList.add('expanded')
            parent = parent.parentElement?.closest('.tree-node')
        }

        this._setActive(el)

        el.scrollIntoView({ behavior: 'smooth', block: 'start' })

        return true
    }
}



initSplitter()
syncToView()
bindInputs()
initEvents()

const tree2 = {
    root: {
        text: 'root',
        children: [
            {
                text: '第一章',
                children: [{
                    text: '第一节'
                }, {
                    text: '第二节'
                }]
            }, {
                text: '第二章',
                children: [{
                    text: '1.xxx'
                }, {
                    text: '2.xxx',
                    children: [{
                        text: '[table]'
                    }, {
                        text: '[table]'
                    }]
                }]
            }
        ]
    }
}

tree = new Tree('#outline', data.tree,{defaultExpanded:false})
let lastXID = null
tree.addEventListener('click', (e) => {
    console.log('node clicked:', e.detail.node)
    const number = e.detail.node.number
    if (typeof number == 'number') {
        document.querySelector(`.page[data-number="${number}"]`).scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
    }

    const xid = e.detail.node.xid
    const els = document.querySelector('.doc').querySelectorAll(`[data-xid="${xid}"]`)
    if (els.length > 0) {
        els[0].scrollIntoView({ behavior: 'smooth', block: 'start' })
    }

})

tree.addEventListener('active',(e)=>{
    console.log('active',e)
    const xid = e.detail.node.xid
    const els = document.querySelector('.doc').querySelectorAll(`[data-xid="${xid}"]`)
    if (lastXID !== xid) {
        document.querySelector('.doc').querySelectorAll(`[data-xid="${lastXID}"]`).forEach(el => {
            el.classList.remove('object-highlight')
        })
    }
    lastXID = xid
    
    if (els.length > 0) {
        els.forEach(el => {
            //可以添加一个class，如：object-selected
            el.classList.toggle('object-highlight')
        })
    }


})

if (!CSS.supports('selector(&)')) {
    alert('您的浏览器不支持最新的css语法，请使用新版本的浏览器，Chrome 112+、Firefox 117+、Safari 16.5+')
}
