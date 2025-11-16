// Webhook management functionality

let guilds = [];
let addedItemsCount = 0;
let removedItemsCount = 0;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    loadGuilds();
    loadVersionInfo();
    setupEventListeners();
});

function setupEventListeners() {
    // Message type change
    document.getElementById('messageType').addEventListener('change', (e) => {
        const type = e.target.value;
        if (type === 'announcement') {
            document.getElementById('announcementLayout').style.display = 'block';
            document.getElementById('updateLayout').style.display = 'none';
        } else {
            document.getElementById('announcementLayout').style.display = 'none';
            document.getElementById('updateLayout').style.display = 'block';
        }
    });
    
    // Announcement destination change
    document.querySelectorAll('input[name="announcementDest"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const guildsDiv = document.getElementById('announcementGuilds');
            guildsDiv.style.display = e.target.value === 'SELECTED' ? 'block' : 'none';
        });
    });
    
    // Version suggestion change
    document.getElementById('versionSuggestion').addEventListener('change', (e) => {
        if (e.target.value) {
            document.getElementById('updateVersion').value = e.target.value;
        }
    });
    
    // Send buttons
    document.getElementById('sendAnnouncementBtn').addEventListener('click', sendAnnouncement);
    document.getElementById('sendUpdateBtn').addEventListener('click', sendUpdate);
}

async function loadGuilds() {
    try {
        const response = await fetch('/api/guilds');
        if (response.ok) {
            const data = await response.json();
            guilds = data.guilds || [];
            populateGuildCheckboxes();
        }
    } catch (error) {
        console.error('Failed to load guilds:', error);
    }
}

function populateGuildCheckboxes() {
    const announcementGuilds = document.getElementById('announcementGuilds');
    const updateGuilds = document.getElementById('updateGuilds');
    
    let html = '';
    guilds.forEach(guild => {
        html += `
            <div class="guild-checkbox">
                <input type="checkbox" id="guild-${guild.id}" value="${guild.id}">
                <label for="guild-${guild.id}">${guild.name}</label>
            </div>
        `;
    });
    
    announcementGuilds.innerHTML = html;
    updateGuilds.innerHTML = html;
}

async function loadVersionInfo() {
    try {
        const response = await fetch('/api/webhook/version');
        if (response.ok) {
            const data = await response.json();
            const select = document.getElementById('versionSuggestion');
            
            // Add suggested versions to dropdown
            data.suggested_versions.forEach(version => {
                const option = document.createElement('option');
                option.value = version;
                option.textContent = version;
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Failed to load version info:', error);
    }
}

function addListItem(type) {
    const listId = type === 'added' ? 'addedList' : 'removedList';
    const list = document.getElementById(listId);
    const itemId = type === 'added' ? ++addedItemsCount : ++removedItemsCount;
    
    const itemDiv = document.createElement('div');
    itemDiv.className = 'list-item';
    itemDiv.id = `${type}-item-${itemId}`;
    itemDiv.innerHTML = `
        <input type="text" placeholder="What was ${type}" class="item-text" data-type="${type}" data-id="${itemId}">
        <input type="text" placeholder="Comment (optional)" class="comment-field" data-type="${type}" data-id="${itemId}">
        <button onclick="removeListItem('${type}', ${itemId})">Remove</button>
    `;
    
    list.appendChild(itemDiv);
}

function removeListItem(type, itemId) {
    const item = document.getElementById(`${type}-item-${itemId}`);
    if (item) {
        item.remove();
    }
}

function getSelectedGuilds(radioName) {
    const destination = document.querySelector(`input[name="${radioName}"]:checked`).value;
    
    if (destination === 'ALL') {
        return { destination: 'ALL', guild_ids: [] };
    } else {
        const checkboxContainer = radioName === 'announcementDest' 
            ? document.getElementById('announcementGuilds')
            : document.getElementById('updateGuilds');
        
        const selectedGuilds = Array.from(checkboxContainer.querySelectorAll('input[type="checkbox"]:checked'))
            .map(cb => cb.value);
        
        return { destination: 'SELECTED', guild_ids: selectedGuilds };
    }
}

async function sendAnnouncement() {
    const btn = document.getElementById('sendAnnouncementBtn');
    const statusDiv = document.getElementById('announcementStatus');
    
    // Get form data
    const title = document.getElementById('announcementTitle').value.trim();
    const author = document.getElementById('announcementAuthor').value.trim();
    const message = document.getElementById('announcementMessage').value.trim();
    
    // Validate
    if (!title || !author || !message) {
        showStatus(statusDiv, 'error', 'Please fill in all fields');
        return;
    }
    
    const { destination, guild_ids } = getSelectedGuilds('announcementDest');
    
    if (destination === 'SELECTED' && guild_ids.length === 0) {
        showStatus(statusDiv, 'error', 'Please select at least one guild');
        return;
    }
    
    // Send request
    btn.disabled = true;
    btn.textContent = 'Sending...';
    
    try {
        const response = await fetch('/api/webhook/send', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                title,
                author,
                message,
                destination,
                guild_ids
            })
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showStatus(statusDiv, 'success', result.message);
            // Clear form
            document.getElementById('announcementTitle').value = '';
            document.getElementById('announcementAuthor').value = '';
            document.getElementById('announcementMessage').value = '';
        } else {
            showStatus(statusDiv, 'error', result.detail || 'Failed to send announcement');
        }
    } catch (error) {
        showStatus(statusDiv, 'error', 'Failed to send announcement: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send Announcement';
    }
}

async function sendUpdate() {
    const btn = document.getElementById('sendUpdateBtn');
    const statusDiv = document.getElementById('updateStatus');
    
    // Get form data
    const version = document.getElementById('updateVersion').value.trim();
    const version_name = document.getElementById('updateVersionName').value.trim();
    const title = document.getElementById('updateTitle').value.trim();
    const source_code = document.getElementById('updateSourceCode').value.trim();
    const additional_message = document.getElementById('updateAdditionalMessage').value.trim();
    
    // Validate
    if (!version || !version_name || !title) {
        showStatus(statusDiv, 'error', 'Please fill in version, version name, and title');
        return;
    }
    
    // Get added items
    const addedItems = [];
    document.querySelectorAll('#addedList .list-item').forEach(item => {
        const text = item.querySelector('.item-text').value.trim();
        const comment = item.querySelector('.comment-field').value.trim();
        if (text) {
            addedItems.push({ text, comment });
        }
    });
    
    // Get removed items
    const removedItems = [];
    document.querySelectorAll('#removedList .list-item').forEach(item => {
        const text = item.querySelector('.item-text').value.trim();
        const comment = item.querySelector('.comment-field').value.trim();
        if (text) {
            removedItems.push({ text, comment });
        }
    });
    
    if (addedItems.length === 0 && removedItems.length === 0) {
        showStatus(statusDiv, 'error', 'Please add at least one item to Added or Removed');
        return;
    }
    
    // Updates always go to ALL guilds
    const destination = 'ALL';
    const guild_ids = [];
    
    // Send request
    btn.disabled = true;
    btn.textContent = 'Sending...';
    
    try {
        const response = await fetch('/api/webhook/update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                version,
                version_name,
                title,
                added: addedItems,
                removed: removedItems,
                source_code,
                additional_message,
                destination,
                guild_ids
            })
        });
        
        const result = await response.json();
        
        if (response.ok) {
            showStatus(statusDiv, 'success', result.message + ' Version updated to ' + result.version);
            // Clear form
            document.getElementById('updateVersion').value = '';
            document.getElementById('updateVersionName').value = '';
            document.getElementById('updateTitle').value = '';
            document.getElementById('updateSourceCode').value = '';
            document.getElementById('updateAdditionalMessage').value = '';
            document.getElementById('addedList').innerHTML = '';
            document.getElementById('removedList').innerHTML = '';
            // Reload version info
            document.getElementById('versionSuggestion').innerHTML = '<option value="">-- Select suggested version --</option>';
            loadVersionInfo();
        } else {
            showStatus(statusDiv, 'error', result.detail || 'Failed to send update');
        }
    } catch (error) {
        showStatus(statusDiv, 'error', 'Failed to send update: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send Update to All Guilds';
    }
}

function showStatus(element, type, message) {
    element.className = `status-message ${type}`;
    element.textContent = message;
    element.style.display = 'block';
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        element.style.display = 'none';
    }, 5000);
}

