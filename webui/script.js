// --- START OF FILE script.js ---

// 全局WebSocket变量
let ws = null;

function connectWebSocket() {
    // 使用你的WebSocket服务器地址和端口
    ws = new WebSocket('ws://localhost:8001');
    
    ws.onopen = function() {
        console.log('WebSocket连接已建立');
    };
    
    ws.onmessage = function(event) {
        // 如果服务器发送"update"消息，则刷新数据
        if (event.data === 'update') {
            console.log('收到更新通知，重新加载数据...');
            loadExpressData();
        }
    };
    
    ws.onclose = function() {
        console.log('WebSocket连接已关闭，将在5秒后尝试重新连接...');
        // 断线重连
        setTimeout(connectWebSocket, 5000);
    };
    
    ws.onerror = function(error) {
        console.error('WebSocket发生错误:', error);
        // 发生错误时，ws.onclose也会被调用，所以重连逻辑会触发
    };
}

// 页面加载完成后，加载初始数据并建立WebSocket连接
document.addEventListener('DOMContentLoaded', function() {
    loadExpressData();
    connectWebSocket();
});

// 重命名函数，更通用
async function loadExpressData() {
    const phoneTail = document.getElementById('phoneSearch').value;
    let url = 'http://localhost:8000/api/express';
    // 如果有搜索条件，则添加到URL中
    if (phoneTail) {
        url += `?phone_tail=${phoneTail}`;
    }

    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        displayExpressData(data);
    } catch (error) {
        console.error('加载快递数据时出错:', error);
        document.getElementById('expressData').innerHTML = '<p>加载数据失败，请检查后端服务是否运行。</p>';
    }
}

// 查询按钮的点击事件函数
function searchExpress() {
    // 直接调用loadExpressData，它会读取输入框的值
    loadExpressData();
}

async function addTestData() {
    const testData = { 
        name: '测试用户' + Math.floor(Math.random() * 100), 
        express_number: 'T' + Date.now().toString().slice(-6), 
        location_code: 'A' + Math.floor(Math.random() * 10), 
        phone_number: '13800138' + Math.floor(Math.random() * 1000).toString().padStart(3, '0') 
    };

    try {
        const response = await fetch('http://localhost:8000/api/express', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(testData)
        });

        if (response.ok) {
            alert('测试数据添加成功！页面将自动更新。');
            // 注意：我们不再需要在这里手动调用loadExpressData()
            // 因为后端会在成功添加数据后通过WebSocket发送更新通知
        } else {
            const errorData = await response.json();
            throw new Error(errorData.error || '添加失败');
        }

    } catch (error) {
        console.error('添加测试数据时出错:', error);
        alert(`添加测试数据时出错: ${error.message}`);
    }
}

function displayExpressData(data) {
    const container = document.getElementById('expressData');
    container.innerHTML = ''; // 清空旧数据

    if (!data || data.length === 0) {
        // 如果搜索框有内容，显示没有匹配结果；否则显示没有快递
        const phoneTail = document.getElementById('phoneSearch').value;
        if (phoneTail) {
            container.innerHTML = `<p>未找到手机尾号为 "${phoneTail}" 的快递信息。</p>`;
        } else {
            container.innerHTML = '<p>当前没有快递信息。</p>';
        }
        return;
    }

    // 按ID降序排序，让最新的数据显示在最上面
    data.sort((a, b) => b.id - a.id);

    data.forEach(item => {
        const div = document.createElement('div');
        div.className = 'express-item';
        div.innerHTML = `
            <p><strong>收件人:</strong> ${item.name}</p>
            <p><strong>快递单号:</strong> ${item.express_number}</p>
            <p><strong>位置:</strong> ${item.location_code}</p>
            <p><strong>手机号:</strong> ${item.phone_number}</p>
        `;
        container.appendChild(div);
    });
}