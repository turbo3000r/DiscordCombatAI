/**
 * Performance page JavaScript
 * Handles 24-hour performance graphs with historical data
 */

let cpuChart, memoryChart, latencyChart;
let updateInterval;
let currentTimeRange = 1440; // Default to 24 hours

// Initialize charts
function initCharts() {
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
            duration: 0 // Disable animation for large datasets
        },
        plugins: {
            legend: {
                display: false
            },
            decimation: {
                enabled: true,
                algorithm: 'lttb',
                samples: 500
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    displayFormats: {
                        hour: 'HH:mm',
                        minute: 'HH:mm'
                    }
                },
                grid: {
                    color: 'rgba(255, 255, 255, 0.1)'
                },
                ticks: {
                    color: '#a0a0a0',
                    maxRotation: 0,
                    autoSkipPadding: 50
                }
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
            datasets: [{
                label: 'CPU Usage (%)',
                data: [],
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: true
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
            datasets: [{
                label: 'Memory Usage (MB)',
                data: [],
                borderColor: 'rgb(255, 99, 132)',
                backgroundColor: 'rgba(255, 99, 132, 0.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: true
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
            datasets: [{
                label: 'Bot Latency (ms)',
                data: [],
                borderColor: 'rgb(153, 102, 255)',
                backgroundColor: 'rgba(153, 102, 255, 0.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: true
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

// Load historical data
async function loadHistoricalData() {
    try {
        const response = await fetch(`/api/metrics/history?minutes=${currentTimeRange}`);
        const data = await response.json();
        
        updateConnectionStatus(true);
        
        // Update CPU chart
        if (data.cpu && data.cpu.length > 0) {
            const cpuData = data.cpu.map(d => ({ x: d.time * 1000, y: d.value }));
            cpuChart.data.datasets[0].data = cpuData;
            cpuChart.update('none');
            
            // Update stats
            updateStats('cpu', data.cpu.map(d => d.value));
        }
        
        // Update Memory chart
        if (data.memory && data.memory.length > 0) {
            const memoryData = data.memory.map(d => ({ x: d.time * 1000, y: d.value }));
            memoryChart.data.datasets[0].data = memoryData;
            memoryChart.update('none');
            
            // Update stats
            updateStats('memory', data.memory.map(d => d.value));
        }
        
        // Update Latency chart
        if (data.latency && data.latency.length > 0) {
            const latencyData = data.latency.map(d => ({ x: d.time * 1000, y: d.value }));
            latencyChart.data.datasets[0].data = latencyData;
            latencyChart.update('none');
            
            // Update stats
            updateStats('latency', data.latency.map(d => d.value));
        }
    } catch (error) {
        console.error('Error loading historical data:', error);
        updateConnectionStatus(false);
    }
}

// Update statistics
function updateStats(metric, values) {
    if (!values || values.length === 0) return;
    
    const current = values[values.length - 1];
    const average = values.reduce((a, b) => a + b, 0) / values.length;
    const peak = Math.max(...values);
    
    const suffix = metric === 'memory' ? ' MB' : (metric === 'cpu' ? '%' : ' ms');
    
    document.getElementById(`${metric}Current`).textContent = current.toFixed(2);
    document.getElementById(`${metric}Average`).textContent = average.toFixed(2);
    document.getElementById(`${metric}Peak`).textContent = peak.toFixed(2);
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

// Initialize
function init() {
    initCharts();
    loadHistoricalData();
    
    // Set up time range selector
    const timeRangeSelect = document.getElementById('timeRange');
    timeRangeSelect.addEventListener('change', (e) => {
        currentTimeRange = parseInt(e.target.value);
        loadHistoricalData();
    });
    
    // Update data every 10 seconds (less frequent for performance page)
    updateInterval = setInterval(loadHistoricalData, 10000);
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (updateInterval) clearInterval(updateInterval);
});

