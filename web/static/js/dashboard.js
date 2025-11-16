/**
 * Dashboard page JavaScript
 * Handles metrics display, charts, and console logs
 */

// Chart instances
let cpuChart, memoryChart, latencyChart;

// Update intervals
let metricsInterval;
let logsSocket = null;
let isPaused = false;
let autoScroll = true;
let reconnectTimeout = null;

// Initialize charts
function initCharts() {
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
            duration: 750
        },
        plugins: {
            legend: {
                display: false
            }
        },
        scales: {
            x: {
                display: false
            },
            y: {
                beginAtZero: true,
                grid: {
                    color: 'rgba(255, 255, 255, 0.1)'
                },
                ticks: {
                    color: '#a0a0a0'
                }
            }
        }
    };

    // CPU Chart
    const cpuCtx = document.getElementById('cpuChart').getContext('2d');
    cpuChart = new Chart(cpuCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'CPU Usage (%)',
                data: [],
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.1)',
                tension: 0.4,
                fill: true,
                pointRadius: 2,
                pointHoverRadius: 4
            }]
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: {
                    ...commonOptions.scales.y,
                    max: 100,
                    ticks: {
                        ...commonOptions.scales.y.ticks,
                        callback: (value) => value + '%'
                    }
                }
            }
        }
    });

    // Memory Chart
    const memoryCtx = document.getElementById('memoryChart').getContext('2d');
    memoryChart = new Chart(memoryCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Memory Usage (MB)',
                data: [],
                borderColor: 'rgb(255, 99, 132)',
                backgroundColor: 'rgba(255, 99, 132, 0.1)',
                tension: 0.4,
                fill: true,
                pointRadius: 2,
                pointHoverRadius: 4
            }]
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: {
                    ...commonOptions.scales.y,
                    ticks: {
                        ...commonOptions.scales.y.ticks,
                        callback: (value) => value + ' MB'
                    }
                }
            }
        }
    });

    // Latency Chart
    const latencyCtx = document.getElementById('latencyChart').getContext('2d');
    latencyChart = new Chart(latencyCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Bot Latency (ms)',
                data: [],
                borderColor: 'rgb(153, 102, 255)',
                backgroundColor: 'rgba(153, 102, 255, 0.1)',
                tension: 0.4,
                fill: true,
                pointRadius: 2,
                pointHoverRadius: 4
            }]
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: {
                    ...commonOptions.scales.y,
                    ticks: {
                        ...commonOptions.scales.y.ticks,
                        callback: (value) => value + ' ms'
                    }
                }
            }
        }
    });
}

// Update metrics
async function updateMetrics() {
    try {
        // Get current metrics
        const metricsData = await fetch('/api/metrics').then(res => res.json());
        
        // Update metric cards
        document.getElementById('uptimeValue').textContent = metricsData.uptime?.formatted || '--';
        document.getElementById('latencyValue').textContent = metricsData.latency ? `${metricsData.latency} ms` : '-- ms';
        document.getElementById('guildsValue').textContent = metricsData.guilds || '0';
        document.getElementById('errorsValue').textContent = metricsData.errors || '0';
        
        // Update graph current values
        document.getElementById('cpuCurrentValue').textContent = `${metricsData.cpu?.toFixed(1) || 0}%`;
        document.getElementById('memoryCurrentValue').textContent = `${metricsData.memory?.mb?.toFixed(1) || 0} MB`;
        document.getElementById('latencyGraphValue').textContent = `${metricsData.latency || 0} ms`;
        
        // Get historical data (last 2 minutes)
        const historyData = await fetch('/api/metrics/history?minutes=2').then(res => res.json());
        
        // Update charts with historical data
        updateChartWithHistory(cpuChart, historyData.cpu);
        updateChartWithHistory(memoryChart, historyData.memory);
        updateChartWithHistory(latencyChart, historyData.latency);
        
        // Update connection status
        updateConnectionStatus(true);
    } catch (error) {
        console.error('Error updating metrics:', error);
        updateConnectionStatus(false);
    }
}

// Update chart with historical data
function updateChartWithHistory(chart, historyData) {
    if (!chart || !historyData || historyData.length === 0) return;
    
    // Convert timestamps to time labels
    const labels = historyData.map(d => new Date(d.time * 1000).toLocaleTimeString());
    const values = historyData.map(d => d.value);
    
    // Update chart data
    chart.data.labels = labels;
    chart.data.datasets[0].data = values;
    
    chart.update('none'); // Update without animation for smoothness
}

function initLogsStreaming() {
    const consoleContent = document.getElementById('consoleContent');
    if (!consoleContent) return;

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    logsSocket = new WebSocket(`${protocol}://${window.location.host}/ws/logs`);

    logsSocket.onopen = () => {
        if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
            reconnectTimeout = null;
        }
        updateConnectionStatus(true);
        consoleContent.innerHTML = '<div class="console-empty">Waiting for logs...</div>';
    };

    logsSocket.onmessage = (event) => {
        if (isPaused) return;
        const line = event.data;
        const atBottom = Math.abs(consoleContent.scrollTop + consoleContent.clientHeight - consoleContent.scrollHeight) < 5;

        if (consoleContent.querySelector('.console-empty') || consoleContent.querySelector('.console-loading')) {
            consoleContent.innerHTML = '';
        }
        const logElement = parseLogLine(line);
        consoleContent.appendChild(logElement);

        if (autoScroll && atBottom) {
            consoleContent.scrollTop = consoleContent.scrollHeight;
        }
    };

    logsSocket.onerror = () => {
        updateConnectionStatus(false);
    };

    logsSocket.onclose = () => {
        updateConnectionStatus(false);
        reconnectTimeout = setTimeout(initLogsStreaming, 3000);
    };

    consoleContent.addEventListener('scroll', () => {
        const atBottom = Math.abs(consoleContent.scrollTop + consoleContent.clientHeight - consoleContent.scrollHeight) < 5;
        autoScroll = atBottom;
    });
}

// Parse log line and add color coding
function parseLogLine(line) {
    const div = document.createElement('div');
    div.className = 'log-line';
    
    // Detect log level and add appropriate class
    if (line.includes('[ERROR]')) {
        div.classList.add('log-error');
    } else if (line.includes('[WARNING]')) {
        div.classList.add('log-warning');
    } else if (line.includes('[INFO]')) {
        div.classList.add('log-info');
    } else if (line.includes('[DEBUG]')) {
        div.classList.add('log-debug');
    } else if (line.includes('[CRITICAL]')) {
        div.classList.add('log-critical');
    }
    
    div.textContent = line;
    return div;
}

// Update connection status
function updateConnectionStatus(isConnected) {
    const statusDot = document.getElementById('connectionStatus');
    const statusText = document.getElementById('connectionText');
    
    if (isConnected) {
        statusDot.className = 'status-dot status-online';
        statusText.textContent = 'Connected';
    } else {
        statusDot.className = 'status-dot status-offline';
        statusText.textContent = 'Disconnected';
    }
}

// Initialize dashboard
function init() {
    initCharts();
    
    // Initial updates
    updateMetrics();
    initLogsStreaming();
    
    // Set up intervals
    metricsInterval = setInterval(updateMetrics, 2000); // Update every 2 seconds
    
    // Pause button
    document.getElementById('pauseBtn').addEventListener('click', () => {
        isPaused = !isPaused;
        const btn = document.getElementById('pauseBtn');
        const info = document.getElementById('consoleInfo');
        
        if (isPaused) {
            btn.textContent = 'Resume';
            info.textContent = 'Auto-refresh: OFF';
        } else {
            btn.textContent = 'Pause';
            info.textContent = 'Auto-refresh: ON';
        }
    });
    
    // Clear button
    document.getElementById('clearBtn').addEventListener('click', () => {
        const consoleContent = document.getElementById('consoleContent');
        consoleContent.innerHTML = '<div class="console-empty">Console cleared</div>';
    });
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (metricsInterval) clearInterval(metricsInterval);
    if (logsSocket) {
        logsSocket.close();
    }
});

