document.addEventListener('DOMContentLoaded', () => {

    // --- 配置 ---
    const API_URL = 'http://localhost:8888/api/express';
    const HISTORY_API_URL = 'http://localhost:8888/api/express/history'; // [新增] 历史API
    const WS_URL = 'ws://localhost:8889';

    // --- DOM 元素获取 ---
    const addForm = document.getElementById('add-form');
    const tableBody = document.getElementById('express-table-body');
    const historyTableBody = document.getElementById('history-table-body'); // [新增]
    const searchInput = document.getElementById('search-input');
    const searchBtn = document.getElementById('search-btn');
    const resetBtn = document.getElementById('reset-btn');
    const wsStatus = document.getElementById('ws-status');

    // --- 函数定义 ---

    /**
     * 从API获取当前快递数据并更新表格
     * @param {string} [phoneTail=''] - 可选的手机尾号用于过滤
     */
    const fetchExpressData = async (phoneTail = '') => {
        try {
            let url = API_URL;
            if (phoneTail) {
                url += `?phone_tail=${encodeURIComponent(phoneTail)}`;
            }
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            renderTable(data);
        } catch (error) {
            console.error("获取当前数据失败:", error);
            tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:red;">数据加载失败</td></tr>`;
        }
    };

    /**
     * [新增] 从API获取历史数据并更新表格
     */
    const fetchHistoryData = async () => {
        try {
            const response = await fetch(HISTORY_API_URL);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            renderHistoryTable(data);
        } catch (error) {
            console.error("获取历史数据失败:", error);
            historyTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:red;">历史数据加载失败</td></tr>`;
        }
    };


    /**
     * 渲染当前库存表格数据
     * @param {Array} data - 从API获取的快递信息数组
     */
    const renderTable = (data) => {
        tableBody.innerHTML = ''; // 清空现有表格
        if (data.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center;">暂无数据</td></tr>`;
            return;
        }
        data.forEach(item => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${item.id}</td>
                <td>${item.name}</td>
                <td>${item.express_number}</td>
                <td>${item.location_code}</td>
                <td>${item.phone_number}</td>
            `;
            tableBody.appendChild(row);
        });
    };

    /**
     * [新增] 渲染历史记录表格数据
     * @param {Array} data - 从API获取的历史信息数组
     */
    const renderHistoryTable = (data) => {
        historyTableBody.innerHTML = '';
        if (data.length === 0) {
            historyTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;">暂无历史记录</td></tr>`;
            return;
        }
        data.forEach(item => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${item.id}</td>
                <td>${item.name}</td>
                <td>${item.express_number}</td>
                <td>${item.location_code}</td>
                <td>${item.phone_number}</td>
                <td>${item.retrieval_time}</td>
            `;
            historyTableBody.appendChild(row);
        });
    };

    /**
     * 连接到 WebSocket 服务器
     */
    const connectWebSocket = () => {
        const ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            console.log('WebSocket 连接成功!');
            wsStatus.textContent = '● 已连接';
            wsStatus.className = 'connected';
        };

        ws.onmessage = (event) => {
            console.log('收到消息:', event.data);
            if (event.data === 'update') {
                console.log('收到更新通知，正在刷新列表...');
                // [修改] 同时刷新当前库存和历史记录
                fetchExpressData(searchInput.value); 
                fetchHistoryData();
            }
        };

        ws.onclose = () => {
            console.log('WebSocket 连接已断开，5秒后尝试重连...');
            wsStatus.textContent = '● 已断开';
            wsStatus.className = 'disconnected';
            setTimeout(connectWebSocket, 5000);
        };

        ws.onerror = (error) => {
            console.error('WebSocket 发生错误:', error);
            wsStatus.textContent = '● 连接错误';
            wsStatus.className = 'disconnected';
            ws.close();
        };
    };


    // ... (事件监听部分保持不变) ...
    addForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(addForm);
        const data = Object.fromEntries(formData.entries());
        try {
            const response = await fetch(API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            if (response.ok) {
                addForm.reset();
            } else {
                const errorData = await response.json();
                alert(`添加失败: ${errorData.error || '未知错误'}`);
            }
        } catch (error) {
            alert('请求失败，请检查网络或联系管理员。');
        }
    });

    searchBtn.addEventListener('click', () => {
        fetchExpressData(searchInput.value.trim());
    });
    resetBtn.addEventListener('click', () => {
        searchInput.value = '';
        fetchExpressData();
    });
    searchInput.addEventListener('keyup', (e) => {
        if (e.key === 'Enter') searchBtn.click();
    });


    // --- 初始化 ---
    // [修改] 页面加载时同时获取当前数据和历史数据
    fetchExpressData(); 
    fetchHistoryData();
    connectWebSocket();
});