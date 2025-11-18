const TYPE_COLORS = {
    minor_issue: "#faa61a",
    major_issue: "#ed4245",
    request: "#5865f2",
    improvement: "#3ba55d",
    feedback: "#9b59b6",
};

const state = {
    suggestions: [],
    filters: {
        type: "",
        categories: [],
        order: "new",
        status: "all",
    },
    selectedId: null,
    loading: false,
};

const elements = {};

function cacheElements() {
    elements.list = document.getElementById("suggestionsList");
    elements.typeFilter = document.getElementById("typeFilter");
    elements.categoryFilter = document.getElementById("categoryFilter");
    elements.orderFilter = document.getElementById("orderFilter");
    elements.statusFilter = document.getElementById("statusFilter");
    elements.clearFiltersBtn = document.getElementById("clearFiltersBtn");
    elements.refreshBtn = document.getElementById("refreshBtn");

    elements.detailPanel = document.getElementById("suggestionDetail");
    elements.detailTitle = document.getElementById("detailTitle");
    elements.detailUser = document.getElementById("detailUser");
    elements.detailGuild = document.getElementById("detailGuild");
    elements.detailCreated = document.getElementById("detailCreated");
    elements.detailTicket = document.getElementById("detailTicket");
    elements.detailStatus = document.getElementById("detailStatus");
    elements.detailTags = document.getElementById("detailTags");
    elements.detailMessage = document.getElementById("detailMessage");
    elements.detailConversation = document.getElementById("detailConversation");
    elements.responseInput = document.getElementById("responseInput");
    elements.responseStatus = document.getElementById("responseStatus");
    elements.sendResponseBtn = document.getElementById("sendResponseBtn");
    elements.markDoneBtn = document.getElementById("markDoneBtn");
    elements.autoFeedbackBtn = document.getElementById("autoFeedbackBtn");
    
    console.log("✅ Elements cached, list element:", elements.list);
}

function registerEvents() {
    elements.typeFilter.addEventListener("change", () => {
        console.log("🔄 Type filter changed to:", elements.typeFilter.value);
        state.filters.type = elements.typeFilter.value;
        fetchSuggestions();
    });

    elements.categoryFilter.addEventListener("change", () => {
        const selected = Array.from(elements.categoryFilter.selectedOptions).map(option => option.value);
        console.log("🔄 Category filter changed to:", selected);
        state.filters.categories = selected;
        fetchSuggestions();
    });

    elements.orderFilter.addEventListener("change", () => {
        console.log("🔄 Order filter changed to:", elements.orderFilter.value);
        state.filters.order = elements.orderFilter.value;
        fetchSuggestions();
    });

    elements.statusFilter.addEventListener("change", () => {
        console.log("🔄 Status filter changed to:", elements.statusFilter.value);
        state.filters.status = elements.statusFilter.value;
        fetchSuggestions();
    });

    elements.clearFiltersBtn.addEventListener("click", () => {
        console.log("🧹 Clearing filters");
        state.filters = { type: "", categories: [], order: "new", status: "all" };
        elements.typeFilter.value = "";
        elements.orderFilter.value = "new";
        elements.statusFilter.value = "all";
        Array.from(elements.categoryFilter.options).forEach(opt => opt.selected = false);
        fetchSuggestions();
    });

    elements.refreshBtn.addEventListener("click", () => {
        console.log("🔄 Manual refresh");
        fetchSuggestions();
    });
    elements.sendResponseBtn.addEventListener("click", () => handleResponse("send"));
    elements.markDoneBtn.addEventListener("click", () => handleResponse("done_no_feedback"));
    elements.autoFeedbackBtn.addEventListener("click", () => handleResponse("done_auto_feedback"));
}

function formatDate(value) {
    if (!value) return "Unknown";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return date.toLocaleString();
}

function getTypeColor(typeValue) {
    return TYPE_COLORS[typeValue] || "#b5bac1";
}

function renderFilterOptions() {
    console.log("🎨 Rendering filter options from", state.suggestions.length, "suggestions");
    const typeOptions = new Map();
    const categoryOptions = new Map();

    state.suggestions.forEach(suggestion => {
        if (suggestion.type) {
            const value = suggestion.type.value || suggestion.type.label;
            typeOptions.set(value, suggestion.type.label || value);
        }
        (suggestion.categories || []).forEach(category => {
            const value = category.value || category.label;
            if (!value) return;
            categoryOptions.set(value, category.label || value);
        });
    });

    console.log("   Types found:", Array.from(typeOptions.entries()));
    console.log("   Categories found:", Array.from(categoryOptions.entries()));

    const currentType = state.filters.type;
    elements.typeFilter.innerHTML = `<option value="">All types</option>`;
    typeOptions.forEach((label, value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        if (value === currentType) option.selected = true;
        elements.typeFilter.appendChild(option);
    });

    const selectedCategories = new Set(state.filters.categories);
    elements.categoryFilter.innerHTML = "";
    if (categoryOptions.size === 0) {
        ["category_1", "category_2", "category_3"].forEach(value => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = value.replace(/_/g, " ");
            elements.categoryFilter.appendChild(option);
        });
    } else {
        categoryOptions.forEach((label, value) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            option.selected = selectedCategories.has(value);
            elements.categoryFilter.appendChild(option);
        });
    }
}

function createSuggestionCard(suggestion) {
    const card = document.createElement("button");
    card.className = "suggestion-card";
    card.type = "button";
    if (suggestion.id === state.selectedId) {
        card.classList.add("active");
    }

    const statusClass = suggestion.responded ? "status-success" : "status-warning";
    const created = formatDate(suggestion.created_at);
    const categories = (suggestion.categories || []).map(cat => cat.label || cat.value).join(", ");
    const typeColor = getTypeColor(suggestion.type?.value);
    
    // Handle both old (no title) and new (with title) suggestions
    const title = suggestion.title || suggestion.message?.slice(0, 60) || "No title";
    const messagePreview = suggestion.message?.slice(0, 80) || "No message";

    card.innerHTML = `
        <div class="suggestion-card-header">
            <div style="flex: 1; min-width: 0;">
                <p class="suggestion-type" style="color: ${typeColor}; display: flex; align-items: center; gap: 0.5rem;">
                    <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: ${typeColor};"></span>
                    ${suggestion.type?.label || "Type"}
                </p>
                <p class="suggestion-title">${title}</p>
                <p class="suggestion-message-preview">${messagePreview}${suggestion.message?.length > 80 ? "…" : ""}</p>
            </div>
            <span class="status-badge ${statusClass}">${suggestion.responded ? "✓ Done" : "Pending"}</span>
        </div>
        <div class="suggestion-card-meta">
            <span>👤 ${suggestion.user?.display_name || "Unknown user"}</span>
            <span>🏰 ${suggestion.guild?.name || "DM"}</span>
            <span>🕒 ${created}</span>
        </div>
        ${categories ? `<div class="suggestion-card-tags">📁 ${categories}</div>` : ""}
    `;

    card.addEventListener("click", () => {
        console.log("🖱️ Clicked suggestion:", suggestion.id);
        state.selectedId = suggestion.id;
        renderSuggestionsList();
        renderDetail();
    });

    return card;
}

function renderSuggestionsList() {
    console.log("📋 renderSuggestionsList called");
    console.log("   Loading:", state.loading);
    console.log("   Suggestions count:", state.suggestions.length);
    console.log("   List element:", elements.list);
    
    if (!elements.list) {
        console.error("❌ List element not found!");
        return;
    }
    
    elements.list.innerHTML = "";
    
    if (state.loading) {
        console.log("   ⏳ Showing loading state");
        elements.list.innerHTML = `<div class="list-placeholder">⏳ Loading suggestions…</div>`;
        return;
    }

    if (!state.suggestions.length) {
        console.log("   📭 No suggestions to show");
        elements.list.innerHTML = `<div class="list-placeholder">📭 No suggestions found. Users can submit feedback using <code>/suggest</code>.</div>`;
        return;
    }

    console.log("   ✅ Creating", state.suggestions.length, "suggestion cards");
    state.suggestions.forEach((suggestion, index) => {
        console.log(`   Creating card ${index + 1}/${state.suggestions.length} for suggestion:`, suggestion.id);
        const card = createSuggestionCard(suggestion);
        elements.list.appendChild(card);
    });
    
    console.log("   ✅ List rendered with", elements.list.children.length, "children");
}

function renderConversationThread(conversation) {
    if (!elements.detailConversation) {
        return;
    }

    const container = elements.detailConversation;
    container.innerHTML = "";

    if (!conversation || !conversation.length) {
        container.innerHTML = `<p class="muted">No conversation yet.</p>`;
        return;
    }

    const sorted = [...conversation].sort((a, b) => {
        const aDate = new Date(a.created_at || a.timestamp || 0).getTime();
        const bDate = new Date(b.created_at || b.timestamp || 0).getTime();
        return aDate - bDate;
    });

    sorted.forEach(entry => {
        const wrapper = document.createElement("div");
        wrapper.className = `conversation-entry conversation-${entry.author_role || "user"}`;

        const header = document.createElement("div");
        header.className = "conversation-entry-header";

        const roleLabel = entry.author_role === "staff" ? "Team" : "User";
        const directionLabel = entry.direction === "outgoing" ? "→" : "←";

        const authorSpan = document.createElement("span");
        authorSpan.textContent = `${roleLabel} ${directionLabel}`;
        header.appendChild(authorSpan);

        const timeSpan = document.createElement("span");
        timeSpan.textContent = formatDate(entry.created_at || entry.timestamp);
        header.appendChild(timeSpan);

        wrapper.appendChild(header);

        if (entry.metadata && entry.metadata.mode) {
            const meta = document.createElement("p");
            meta.className = "conversation-entry-meta";
            const modeLabel = String(entry.metadata.mode).replace(/_/g, " ");
            meta.textContent = `Mode: ${modeLabel}`;
            wrapper.appendChild(meta);
        }

        const body = document.createElement("p");
        body.className = "conversation-entry-body";
        body.textContent = entry.text || "No text provided.";
        wrapper.appendChild(body);

        container.appendChild(wrapper);
    });
}

function renderDetail() {
    console.log("📄 renderDetail called, selectedId:", state.selectedId);
    const suggestion = state.suggestions.find(item => item.id === state.selectedId);
    console.log("   Found suggestion:", suggestion ? suggestion.id : "none");
    
    const isEmpty = !suggestion;
    
    // Toggle visibility properly
    if (isEmpty) {
        console.log("   Setting empty state");
        elements.detailPanel.classList.add("empty");
        elements.responseInput.value = "";
        elements.responseStatus.textContent = "";
        if (elements.detailConversation) {
            elements.detailConversation.innerHTML = "";
        }
        return;
    }
    
    console.log("   Rendering detail for:", suggestion.id);
    elements.detailPanel.classList.remove("empty");

    const typeColor = getTypeColor(suggestion.type?.value);
    const displayTitle = suggestion.title || suggestion.message?.slice(0, 100) || "Untitled Suggestion";
    elements.detailTitle.textContent = displayTitle;
    elements.detailTitle.style.color = typeColor;
    
    elements.detailUser.innerHTML = `👤 <strong>User:</strong> ${suggestion.user?.display_name || suggestion.user?.name || "Unknown"}`;
    elements.detailGuild.innerHTML = `🏰 <strong>Guild:</strong> ${suggestion.guild?.name || "Direct message"}`;
    elements.detailCreated.innerHTML = `🕒 <strong>Created:</strong> ${formatDate(suggestion.created_at)}`;
    if (elements.detailTicket) {
        elements.detailTicket.innerHTML = `🎫 <strong>Ticket:</strong> ${suggestion.ticket_uid || "N/A"}`;
    }
    elements.detailStatus.textContent = suggestion.responded ? "✓ Responded" : "⏳ Pending";
    elements.detailStatus.className = `status-badge ${suggestion.responded ? "status-success" : "status-warning"}`;

    elements.detailTags.innerHTML = `
        <span class="tag-pill" style="background-color: ${typeColor}20; border-color: ${typeColor}; color: ${typeColor};">
            ${suggestion.type?.label || "Type"}
        </span>
    `;
    (suggestion.categories || []).forEach(category => {
        const tag = document.createElement("span");
        tag.className = "tag-pill";
        tag.textContent = category.label || category.value;
        elements.detailTags.appendChild(tag);
    });
    if (!(suggestion.categories || []).length && suggestion.type) {
        const tag = document.createElement("span");
        tag.className = "tag-pill";
        tag.textContent = "No categories";
        tag.style.opacity = "0.6";
        elements.detailTags.appendChild(tag);
    }

    elements.detailMessage.textContent = suggestion.message || "No message provided.";

    renderConversationThread(suggestion.conversation || []);

    elements.responseInput.value = suggestion.response_text || "";
    const disabled = suggestion.responded;
    [elements.responseInput, elements.sendResponseBtn, elements.markDoneBtn, elements.autoFeedbackBtn].forEach(el => {
        el.disabled = !suggestion || (el === elements.responseInput ? false : disabled);
    });

    if (suggestion.responded) {
        elements.responseStatus.textContent = "✓ This suggestion has already been marked as responded.";
        elements.responseStatus.className = "response-status info";
    } else {
        elements.responseStatus.textContent = "";
        elements.responseStatus.className = "response-status";
    }
    
    console.log("   ✅ Detail rendered");
}

async function fetchSuggestions() {
    console.log("🌐 fetchSuggestions called");
    console.log("   Current filters:", state.filters);
    
    state.loading = true;
    renderSuggestionsList();
    
    try {
        const params = new URLSearchParams();
        if (state.filters.type) params.set("type", state.filters.type);
        if (state.filters.categories.length) params.set("categories", state.filters.categories.join(","));
        params.set("order", state.filters.order);
        
        const url = `/api/suggestions?${params.toString()}`;
        console.log("   Fetching from:", url);
        
        const response = await fetch(url);
        console.log("   Response status:", response.status);
        
        if (!response.ok) throw new Error("Failed to load suggestions");
        
        const data = await response.json();
        console.log("   Raw API response:", data);
        
        let allSuggestions = data.items || [];
        
        // Apply client-side status filter
        if (state.filters.status === "pending") {
            allSuggestions = allSuggestions.filter(s => !s.responded);
        } else if (state.filters.status === "done") {
            allSuggestions = allSuggestions.filter(s => s.responded);
        }
        
        state.suggestions = allSuggestions;
        console.log("   ✅ Loaded", state.suggestions.length, "suggestions (after status filter)");
        console.log("   Suggestions:", state.suggestions);
        
        // Reset selection if current no longer exists
        if (!state.suggestions.find(item => item.id === state.selectedId)) {
            state.selectedId = state.suggestions[0]?.id || null;
            console.log("   Selected first suggestion:", state.selectedId);
        }
        
        state.loading = false;
        
        console.log("   Calling renderFilterOptions...");
        renderFilterOptions();
        
        console.log("   Calling renderSuggestionsList...");
        renderSuggestionsList();
        
        console.log("   Calling renderDetail...");
        renderDetail();
        
        console.log("   ✅ fetchSuggestions complete");
    } catch (error) {
        console.error("❌ Error loading suggestions:", error);
        state.loading = false;
        elements.list.innerHTML = `<div class="list-error">❌ Unable to load suggestions. Please try again later.<br><small>${error.message}</small></div>`;
    }
}

async function handleResponse(mode) {
    if (!state.selectedId) return;
    const suggestion = state.suggestions.find(item => item.id === state.selectedId);
    if (!suggestion || suggestion.responded) return;

    const payload = { mode, response_text: null };
    if (mode === "send") {
        const text = elements.responseInput.value.trim();
        if (!text) {
            elements.responseStatus.textContent = "⚠️ Response text is required to send a DM.";
            elements.responseStatus.className = "response-status error";
            return;
        }
        payload.response_text = text;
    }

    elements.responseStatus.textContent = "⏳ Sending...";
    elements.responseStatus.className = "response-status info";
    [elements.sendResponseBtn, elements.markDoneBtn, elements.autoFeedbackBtn].forEach(btn => btn.disabled = true);

    try {
        const response = await fetch(`/api/suggestions/${state.selectedId}/respond`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || "Failed to respond to suggestion");
        }
        const updated = await response.json();
        state.suggestions = state.suggestions.map(item => item.id === updated.id ? updated : item);
        elements.responseStatus.textContent = "✅ Suggestion updated successfully.";
        elements.responseStatus.className = "response-status success";
        renderSuggestionsList();
        renderDetail();
        await fetchSuggestions();
    } catch (error) {
        console.error(error);
        elements.responseStatus.textContent = `❌ ${error.message}`;
        elements.responseStatus.className = "response-status error";
    } finally {
        [elements.sendResponseBtn, elements.markDoneBtn, elements.autoFeedbackBtn].forEach(btn => btn.disabled = false);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    console.log("🚀 Page loaded, initializing...");
    cacheElements();
    registerEvents();
    fetchSuggestions();
});
