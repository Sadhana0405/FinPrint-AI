/**
 * FinPrint AI — Client-Side SaaS Interactions & Form Validation
 */

(function () {
    "use strict";

    // --------------------------------------------------------------------------
    // File Helpers & Formatting
    // --------------------------------------------------------------------------
    function getFileExtension(filename) {
        if (!filename || filename.indexOf(".") === -1) return "FILE";
        return filename.split(".").pop().toUpperCase();
    }

    function formatFileSize(bytes) {
        if (!bytes || bytes === 0) return "";
        if (bytes < 1024) return bytes + " B";
        var kb = bytes / 1024;
        if (kb < 1024) return kb.toFixed(1) + " KB";
        var mb = kb / 1024;
        return mb.toFixed(1) + " MB";
    }

    function getFilesFromInput(input) {
        return Array.from(input.files || []);
    }

    function setInputFiles(input, filesArray) {
        var dt = new DataTransfer();
        filesArray.forEach(function (file) {
            dt.items.add(file);
        });
        input.files = dt.files;
    }

    // --------------------------------------------------------------------------
    // Modern Dropzone & File Chips
    // --------------------------------------------------------------------------
    var ALLOWED_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"];

    function renderDropzoneChips(zone) {
        var input = zone.querySelector(".dropzone-input");
        var list = zone.querySelector("[data-chip-list]");
        if (!input || !list) return;

        list.innerHTML = "";
        var files = getFilesFromInput(input);

        files.forEach(function (file, index) {
            var li = document.createElement("li");
            li.className = "file-chip";

            var ext = getFileExtension(file.name);
            var sizeStr = file.size ? formatFileSize(file.size) : "";

            var extensionLabel = document.createElement("span");
            extensionLabel.className = "file-chip-ext";
            extensionLabel.textContent = ext;
            var filenameLabel = document.createElement("span");
            filenameLabel.className = "file-chip-name";
            filenameLabel.title = file.name;
            filenameLabel.textContent = file.name;
            li.appendChild(extensionLabel);
            li.appendChild(filenameLabel);
            if (sizeStr) {
                var sizeLabel = document.createElement("small");
                sizeLabel.className = "muted";
                sizeLabel.style.fontSize = "0.75rem";
                sizeLabel.textContent = sizeStr;
                li.appendChild(sizeLabel);
            }

            var removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "remove-file";
            removeBtn.setAttribute("aria-label", "Remove " + file.name);
            removeBtn.innerHTML = "&times;";

            removeBtn.addEventListener("click", function (e) {
                e.stopPropagation();
                var remaining = getFilesFromInput(input).filter(function (_, i) {
                    return i !== index;
                });
                setInputFiles(input, remaining);
                renderDropzoneChips(zone);
            });

            li.appendChild(removeBtn);
            list.appendChild(li);
        });
    }

    function appendFilesToZone(zone, newFiles) {
        var input = zone.querySelector(".dropzone-input");
        if (!input) return;

        var current = getFilesFromInput(input);
        Array.from(newFiles).forEach(function (file) {
            var ext = (file.name.split(".").pop() || "").toLowerCase();
            if (ALLOWED_EXTENSIONS.indexOf(ext) !== -1) {
                // Check if file already added (by name & size)
                var exists = current.some(function (f) {
                    return f.name === file.name && f.size === file.size;
                });
                if (!exists) {
                    current.push(file);
                }
            }
        });

        setInputFiles(input, current);
        renderDropzoneChips(zone);
    }

    document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
        var input = zone.querySelector(".dropzone-input");
        var browseBtn = zone.querySelector("[data-browse]");

        if (browseBtn && input) {
            browseBtn.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                input.click();
            });
        }

        if (input) {
            input.addEventListener("change", function () {
                renderDropzoneChips(zone);
            });
        }

        zone.addEventListener("dragover", function (e) {
            e.preventDefault();
            zone.classList.add("is-dragover");
        });

        zone.addEventListener("dragleave", function () {
            zone.classList.remove("is-dragover");
        });

        zone.addEventListener("drop", function (e) {
            e.preventDefault();
            zone.classList.remove("is-dragover");
            if (e.dataTransfer && e.dataTransfer.files) {
                appendFilesToZone(zone, e.dataTransfer.files);
            }
        });
    });

    // --------------------------------------------------------------------------
    // Quick Prompt Suggestion Chips
    // --------------------------------------------------------------------------
    document.querySelectorAll("[data-prompt-chip]").forEach(function (chip) {
        chip.addEventListener("click", function () {
            var targetId = chip.getAttribute("data-target") || "instruction";
            var textarea = document.getElementById(targetId);
            var text = chip.getAttribute("data-prompt-chip");
            if (!textarea || !text) return;

            var current = textarea.value.trim();
            if (!current) {
                textarea.value = text;
            } else if (current.indexOf(text) === -1) {
                textarea.value = current + "; " + text;
            }
            textarea.focus();
        });
    });

    // --------------------------------------------------------------------------
    // Hospital Verifier: Dynamic Manual Medicines
    // --------------------------------------------------------------------------
    var medicineRowsContainer = document.getElementById("medicine-rows");
    var addMedicineBtn = document.getElementById("add-medicine");
    var medicineRowNumber = 1;

    function createMedicineRow(nameVal, qtyVal) {
        var row = document.createElement("div");
        row.className = "medicine-row";
        var nameLabel = document.createElement("label");
        nameLabel.className = "sr-only";
        nameLabel.htmlFor = "med-name-" + medicineRowNumber;
        nameLabel.textContent = "Medicine name";
        var nameInput = document.createElement("input");
        nameInput.id = "med-name-" + medicineRowNumber;
        nameInput.name = "medicine_name";
        nameInput.type = "text";
        nameInput.placeholder = "e.g. Paracetamol 650mg, Meropenem 1g";
        nameInput.value = nameVal || "";
        nameLabel.appendChild(nameInput);
        var quantityLabel = document.createElement("label");
        quantityLabel.className = "sr-only";
        quantityLabel.htmlFor = "med-qty-" + medicineRowNumber;
        quantityLabel.textContent = "Quantity";
        var quantityInput = document.createElement("input");
        quantityInput.id = "med-qty-" + medicineRowNumber;
        quantityInput.name = "quantity";
        quantityInput.type = "text";
        quantityInput.placeholder = "e.g. 10 tablets, 2 vials";
        quantityInput.value = qtyVal || "";
        quantityLabel.appendChild(quantityInput);
        var removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "remove-row-btn";
        removeButton.setAttribute("aria-label", "Remove medicine row");
        removeButton.title = "Remove row";
        removeButton.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6"/>' +
            '</svg>';
        row.appendChild(nameLabel);
        row.appendChild(nameInput);
        row.appendChild(quantityLabel);
        row.appendChild(quantityInput);
        row.appendChild(removeButton);
        medicineRowNumber += 1;

        var removeBtn = row.querySelector(".remove-row-btn");
        removeBtn.addEventListener("click", function () {
            var allRows = medicineRowsContainer.querySelectorAll(".medicine-row");
            if (allRows.length > 1) {
                row.remove();
            } else {
                // Clear fields if only one row
                row.querySelectorAll("input").forEach(function (inp) { inp.value = ""; });
            }
        });

        return row;
    }

    if (addMedicineBtn && medicineRowsContainer) {
        // Attach listener to initial row's remove button if present
        var initialRemove = medicineRowsContainer.querySelector(".remove-row-btn");
        if (initialRemove) {
            initialRemove.addEventListener("click", function () {
                var row = initialRemove.closest(".medicine-row");
                var allRows = medicineRowsContainer.querySelectorAll(".medicine-row");
                if (allRows.length > 1) {
                    row.remove();
                } else {
                    row.querySelectorAll("input").forEach(function (inp) { inp.value = ""; });
                }
            });
        }

        addMedicineBtn.addEventListener("click", function () {
            var newRow = createMedicineRow();
            medicineRowsContainer.appendChild(newRow);
            var firstInput = newRow.querySelector("input");
            if (firstInput) firstInput.focus();
        });
    }

    // --------------------------------------------------------------------------
    // Hospital Form Client Validation (Ensure at least ONE evidence source)
    // --------------------------------------------------------------------------
    var hospitalForm = document.getElementById("hospital-form");
    if (hospitalForm) {
        hospitalForm.addEventListener("submit", function (e) {
            var billInput = hospitalForm.querySelector('input[name="hospital_bill"]');
            var presInput = hospitalForm.querySelector('input[name="prescription"]');
            var proofInput = hospitalForm.querySelector('input[name="medicine_proof"]');

            var hasFiles = (billInput && billInput.files && billInput.files.length > 0) ||
                           (presInput && presInput.files && presInput.files.length > 0) ||
                           (proofInput && proofInput.files && proofInput.files.length > 0);

            var hasManualMed = false;
            var medNames = hospitalForm.querySelectorAll('input[name="medicine_name"]');
            medNames.forEach(function (inp) {
                if (inp.value && inp.value.trim().length > 0) {
                    hasManualMed = true;
                }
            });

            if (!hasFiles && !hasManualMed) {
                e.preventDefault();
                var alertEl = document.getElementById("hospital-validation-alert");
                if (!alertEl) {
                    alertEl = document.createElement("div");
                    alertEl.id = "hospital-validation-alert";
                    alertEl.className = "flash flash-warning";
                    alertEl.style.marginBottom = "16px";
                    alertEl.innerHTML =
                        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01"/></svg>' +
                        '<span>Please provide at least one piece of evidence (hospital bill, prescription, purchase receipt, or a manual medicine) to proceed.</span>';
                    hospitalForm.parentNode.insertBefore(alertEl, hospitalForm);
                }
                alertEl.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    }

    // --------------------------------------------------------------------------
    // Password Show / Hide Toggle
    // --------------------------------------------------------------------------
    document.querySelectorAll(".js-toggle-password").forEach(function (button) {
        button.addEventListener("click", function () {
            var targetId = button.getAttribute("data-target");
            var field = document.getElementById(targetId);
            if (!field) return;

            var isHidden = field.type === "password";
            field.type = isHidden ? "text" : "password";

            // Update SVG icon and aria-label
            if (isHidden) {
                button.setAttribute("aria-label", "Hide password");
                button.innerHTML =
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
                    '  <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/>' +
                    '  <line x1="1" y1="1" x2="23" y2="23"/>' +
                    '</svg>';
            } else {
                button.setAttribute("aria-label", "Show password");
                button.innerHTML =
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
                    '  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
                    '  <circle cx="12" cy="12" r="3"/>' +
                    '</svg>';
            }
        });
    });

    // --------------------------------------------------------------------------
    // Responsive Mobile Sidebar Navigation
    // --------------------------------------------------------------------------
    var sidebar = document.getElementById("sidebar");
    var backdrop = document.querySelector("[data-close-nav]");
    var openNavBtn = document.querySelector("[data-open-nav]");

    function setMobileNav(open, restoreFocus) {
        if (!sidebar) return;
        sidebar.classList.toggle("is-open", open);
        if (backdrop) {
            backdrop.hidden = !open;
        }
        if (openNavBtn) {
            openNavBtn.setAttribute("aria-expanded", open ? "true" : "false");
            if (!open && restoreFocus) openNavBtn.focus();
        }
    }

    if (openNavBtn) {
        openNavBtn.addEventListener("click", function () {
            setMobileNav(true);
        });
    }

    if (backdrop) {
        backdrop.addEventListener("click", function () {
            setMobileNav(false, true);
        });
    }

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            setMobileNav(false, true);
        }
    });

    document.querySelectorAll(".side-nav a, .side-foot a").forEach(function (link) {
        link.addEventListener("click", function () {
            setMobileNav(false);
        });
    });

    // --------------------------------------------------------------------------
    // History Instant Search & Module Filter
    // --------------------------------------------------------------------------
    var historySearch = document.getElementById("history-search");
    var filterPills = document.querySelectorAll("[data-filter-module]");

    function filterHistory() {
        var query = (historySearch ? historySearch.value : "").toLowerCase().trim();
        var activeFilterPill = document.querySelector(".filter-pill.is-active");
        var activeModule = activeFilterPill ? activeFilterPill.getAttribute("data-filter-module") : "all";

        // Desktop table rows
        var rows = document.querySelectorAll("tbody tr[data-module]");
        rows.forEach(function (row) {
            var rowModule = row.getAttribute("data-module") || "";
            var text = row.textContent.toLowerCase();

            var matchesModule = (activeModule === "all" || rowModule === activeModule);
            var matchesQuery = (!query || text.indexOf(query) !== -1);

            row.style.display = (matchesModule && matchesQuery) ? "" : "none";
        });

        // Mobile cards
        var cards = document.querySelectorAll(".history-card-mobile");
        cards.forEach(function (card) {
            var cardModule = card.getAttribute("data-module") || "";
            var text = card.textContent.toLowerCase();

            var matchesModule = (activeModule === "all" || cardModule === activeModule);
            var matchesQuery = (!query || text.indexOf(query) !== -1);

            card.style.display = (matchesModule && matchesQuery) ? "" : "none";
        });
    }

    if (historySearch) {
        historySearch.addEventListener("input", filterHistory);
    }

    filterPills.forEach(function (pill) {
        pill.addEventListener("click", function () {
            filterPills.forEach(function (p) {
                p.classList.remove("is-active");
                p.setAttribute("aria-pressed", "false");
            });
            pill.classList.add("is-active");
            pill.setAttribute("aria-pressed", "true");
            filterHistory();
        });
    });

    // --------------------------------------------------------------------------
    // Copy Text Button (for Extracted Text Viewer in Result Page)
    // --------------------------------------------------------------------------
    document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var targetId = btn.getAttribute("data-copy-target");
            var el = document.getElementById(targetId);
            if (!el) return;

            var textToCopy = el.innerText || el.textContent;
            navigator.clipboard.writeText(textToCopy).then(function () {
                var originalText = btn.innerHTML;
                btn.innerHTML =
                    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="display:inline;vertical-align:middle;margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg> Copied!';
                setTimeout(function () {
                    btn.innerHTML = originalText;
                }, 2000);
            }).catch(function () {
                // Fallback
                btn.textContent = "Copied!";
                setTimeout(function () { btn.textContent = "Copy text"; }, 2000);
            });
        });
    });

    // --------------------------------------------------------------------------
    // Logout Confirmation
    // --------------------------------------------------------------------------
    document.querySelectorAll(".js-logout").forEach(function (link) {
        link.addEventListener("click", function (e) {
            if (!window.confirm("Log out of FinPrint AI?")) {
                e.preventDefault();
            }
        });
    });

    // --------------------------------------------------------------------------
    // History actions and confirmation dialogs
    // --------------------------------------------------------------------------
    var editTitleModal = document.getElementById("edit-title-modal");
    var deleteModal = document.getElementById("delete-confirm-modal");
    var deleteAllModal = document.getElementById("delete-all-modal");
    var editTitleForm = document.getElementById("edit-title-form");
    var editTitleInput = document.getElementById("edit-title-input");
    var deleteForm = document.getElementById("delete-analysis-form");
    var deleteTitle = document.getElementById("delete-item-title");
    var lastModalTrigger = null;

    function closeModals() {
        [editTitleModal, deleteModal, deleteAllModal].forEach(function (modal) {
            if (modal) modal.hidden = true;
        });
        if (lastModalTrigger) lastModalTrigger.focus();
        lastModalTrigger = null;
    }

    document.querySelectorAll("[data-edit-title]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (!editTitleModal || !editTitleForm || !editTitleInput) return;
            editTitleForm.action = button.getAttribute("data-action");
            editTitleInput.value = button.getAttribute("data-title") || "";
            lastModalTrigger = button;
            editTitleModal.hidden = false;
            editTitleInput.focus();
        });
    });

    document.querySelectorAll("[data-delete-analysis]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (!deleteModal || !deleteForm || !deleteTitle) return;
            deleteForm.action = button.getAttribute("data-action");
            deleteTitle.textContent = button.getAttribute("data-title") || "this review";
            lastModalTrigger = button;
            deleteModal.hidden = false;
            deleteModal.querySelector(".modal-close").focus();
        });
    });

    document.querySelectorAll("[data-delete-all]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (deleteAllModal) {
                lastModalTrigger = button;
                deleteAllModal.hidden = false;
                deleteAllModal.querySelector(".modal-close").focus();
            }
        });
    });

    document.querySelectorAll(".modal-close").forEach(function (button) {
        button.addEventListener("click", closeModals);
    });

    document.querySelectorAll(".modal-backdrop").forEach(function (modal) {
        modal.addEventListener("click", function (event) {
            if (event.target === modal) closeModals();
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeModals();
    });

})();
