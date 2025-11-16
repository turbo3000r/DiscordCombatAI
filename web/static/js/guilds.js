/**
 * Guilds page JavaScript
 * Handles guild list display, selection, and resizable panels
 */

let currentGuildId = null;
let isResizing = false;

// Initialize guilds page
function init() {
    loadGuilds();
    setupResizer();
    setupRefreshButton();
    
    // Initial connection status
    updateConnectionStatus(false);
}

// Load guilds from API
async function loadGuilds() {
    try {
        const data = await fetch('/api/guilds').then(res => res.json());
        
        updateConnectionStatus(true);
        displayGuilds(data.guilds);
        
        // Update count
        document.getElementById('guildCount').textContent = data.count || 0;
    } catch (error) {
        console.error('Error loading guilds:', error);
        updateConnectionStatus(false);
        showError('Failed to load guilds. Please try again.');
    }
}

// Display guilds in the list
function displayGuilds(guilds) {
    const guildList = document.getElementById('guildList');
    
    if (!guilds || guilds.length === 0) {
        guildList.innerHTML = '<div class="no-guilds">No guilds found</div>';
        return;
    }
    
    // Clear existing content
    guildList.innerHTML = '';
    
    // Create guild cards
    guilds.forEach(guild => {
        const card = createGuildCard(guild);
        guildList.appendChild(card);
    });
}

// Create a guild card element
function createGuildCard(guild) {
    const card = document.createElement('div');
    card.className = 'guild-card';
    card.dataset.guildId = guild.id;
    
    // Guild icon
    const icon = document.createElement('div');
    icon.className = 'guild-icon';
    
    if (guild.icon_url) {
        const img = document.createElement('img');
        img.src = guild.icon_url;
        img.alt = guild.name;
        img.onerror = () => {
            // Fallback to text icon
            icon.textContent = guild.name.charAt(0).toUpperCase();
            icon.classList.add('guild-icon-text');
        };
        icon.appendChild(img);
    } else {
        // Text icon with first letter
        icon.textContent = guild.name.charAt(0).toUpperCase();
        icon.classList.add('guild-icon-text');
    }
    
    // Guild info
    const info = document.createElement('div');
    info.className = 'guild-info';
    
    const name = document.createElement('div');
    name.className = 'guild-name';
    name.textContent = guild.name;
    
    const members = document.createElement('div');
    members.className = 'guild-members';
    members.textContent = `${guild.member_count} members`;
    
    const guildId = document.createElement('div');
    guildId.className = 'guild-id';
    guildId.textContent = `ID: ${guild.id}`;
    
    // Webhook status indicator
    const webhookStatus = document.createElement('div');
    webhookStatus.className = 'guild-webhook-status';
    webhookStatus.style.marginTop = '8px';
    webhookStatus.style.fontSize = '0.85rem';
    if (guild.webhook_configured) {
        webhookStatus.innerHTML = '✅ Webhook configured';
        webhookStatus.style.color = 'var(--success-color)';
    } else {
        webhookStatus.innerHTML = '⚠️ Webhook not configured';
        webhookStatus.style.color = 'var(--warning-color)';
    }
    
    info.appendChild(name);
    info.appendChild(members);
    info.appendChild(guildId);
    info.appendChild(webhookStatus);
    
    card.appendChild(icon);
    card.appendChild(info);
    
    // Click handler
    card.addEventListener('click', () => selectGuild(guild.id));
    
    return card;
}

// Select a guild and load its details
async function selectGuild(guildId) {
    // Update UI to show selection
    document.querySelectorAll('.guild-card').forEach(card => {
        card.classList.remove('selected');
    });
    
    const selectedCard = document.querySelector(`[data-guild-id="${guildId}"]`);
    if (selectedCard) {
        selectedCard.classList.add('selected');
    }
    
    currentGuildId = guildId;
    
    // Load guild details
    try {
        const guild = await fetch(`/api/guilds/${guildId}`).then(res => res.json());
        displayGuildDetails(guild);
    } catch (error) {
        console.error('Error loading guild details:', error);
        showError('Failed to load guild details.');
    }
}

// Display guild details in the right panel
function displayGuildDetails(guild) {
    const detailsContainer = document.getElementById('guildDetails');
    
    detailsContainer.innerHTML = '';
    
    // Create details view
    const detailsView = document.createElement('div');
    detailsView.className = 'guild-details-view';
    
    // Guild header
    const header = document.createElement('div');
    header.className = 'guild-details-header';
    
    if (guild.icon_url) {
        const icon = document.createElement('img');
        icon.src = guild.icon_url;
        icon.alt = guild.name;
        icon.className = 'guild-details-icon';
        header.appendChild(icon);
    }
    
    const headerInfo = document.createElement('div');
    headerInfo.className = 'guild-details-header-info';
    
    const name = document.createElement('h2');
    name.textContent = guild.name;
    
    const id = document.createElement('p');
    id.className = 'text-muted';
    id.textContent = `Guild ID: ${guild.id}`;
    
    headerInfo.appendChild(name);
    headerInfo.appendChild(id);
    header.appendChild(headerInfo);
    
    // Basic info section
    const basicInfo = document.createElement('div');
    basicInfo.className = 'guild-details-section';
    basicInfo.innerHTML = `
        <h3>Basic Information</h3>
        <div class="guild-detail-row">
            <span class="detail-label">Member Count:</span>
            <span class="detail-value">${guild.member_count}</span>
        </div>
        <div class="guild-detail-row">
            <span class="detail-label">Created:</span>
            <span class="detail-value">${guild.created_at ? new Date(guild.created_at).toLocaleDateString() : 'N/A'}</span>
        </div>
    `;
    
    // Configuration section
    if (guild.config && Object.keys(guild.config).length > 0) {
        const configInfo = document.createElement('div');
        configInfo.className = 'guild-details-section';
        configInfo.innerHTML = `
            <h3>Bot Configuration</h3>
            <div class="guild-detail-row">
                <span class="detail-label">Language:</span>
                <span class="detail-value">${guild.config.language || 'Not set'}</span>
            </div>
            <div class="guild-detail-row">
                <span class="detail-label">Model:</span>
                <span class="detail-value">${guild.config.model || 'Not set'}</span>
            </div>
            <div class="guild-detail-row">
                <span class="detail-label">Enabled:</span>
                <span class="detail-value">${guild.config.enabled ? '✅ Yes' : '❌ No'}</span>
            </div>
        `;
        detailsView.appendChild(header);
        detailsView.appendChild(basicInfo);
        detailsView.appendChild(configInfo);
    } else {
        detailsView.appendChild(header);
        detailsView.appendChild(basicInfo);
    }
    
    // Placeholder message
    const placeholder = document.createElement('div');
    placeholder.className = 'guild-details-section placeholder-section';
    placeholder.innerHTML = `
        <div class="placeholder-message">
            <p class="text-muted">📊 Full guild statistics and management features coming soon</p>
        </div>
    `;
    
    detailsView.appendChild(placeholder);
    detailsContainer.appendChild(detailsView);
}

// Setup panel resizer
function setupResizer() {
    const resizer = document.getElementById('panelResizer');
    const leftPanel = document.getElementById('leftPanel');
    const rightPanel = document.getElementById('rightPanel');
    
    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });
    
    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        
        const container = document.querySelector('.split-panel-container');
        const containerRect = container.getBoundingClientRect();
        const mouseX = e.clientX - containerRect.left;
        
        // Calculate percentage (min 15%, max 50%)
        let percentage = (mouseX / containerRect.width) * 100;
        percentage = Math.max(15, Math.min(50, percentage));
        
        leftPanel.style.width = `${percentage}%`;
        rightPanel.style.width = `${100 - percentage}%`;
    });
    
    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

// Setup refresh button
function setupRefreshButton() {
    const refreshBtn = document.getElementById('refreshBtn');
    refreshBtn.addEventListener('click', async () => {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
        
        await loadGuilds();
        
        refreshBtn.disabled = false;
        refreshBtn.textContent = 'Refresh';
    });
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

// Show error message
function showError(message) {
    const guildList = document.getElementById('guildList');
    guildList.innerHTML = `<div class="error-message">${message}</div>`;
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

