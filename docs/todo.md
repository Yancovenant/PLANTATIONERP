# INPHMS ERP - Development TODO

## [UPDATED] Current Status: Advanced CLI Framework + Partial Server Implementation
**What you have:** Command-line interface with server framework, HTTP layer, and database connectivity
**What you need:** Complete ERP system that runs as .exe with MariaDB

---

## [COMPLETED] ✅ Phase 1: CLI Framework & Server Structure (DONE!)
**Goal:** Basic command-line interface and server framework

### 1.1 ✅ CLI Command System
- **File:** `inphms/cli/command.py` - **Status:** ✅ COMPLETED
- **Features:** Command registration, help system, argument parsing
- **What works:** Command discovery, argument handling, module loading

### 1.2 ✅ Server Entry Point
- **File:** `inphms/cli/server.py` - **Status:** ✅ COMPLETED  
- **Features:** Main server function, PostgreSQL user checks, configuration
- **What works:** Server startup, config parsing, database validation

### 1.3 ✅ HTTP Layer Foundation
- **File:** `inphms/http.py` - **Status:** ✅ COMPLETED
- **Features:** WSGI application class, request routing foundation
- **What works:** Basic HTTP application structure

### 1.4 ✅ Configuration System
- **File:** `inphms/tools/config.py` - **Status:** ✅ COMPLETED
- **Features:** Config file parsing, command-line options, database settings
- **What works:** Full configuration management system

### 1.5 ✅ Database Layer
- **File:** `inphms/sql_db.py` - **Status:** ✅ COMPLETED
- **Features:** PostgreSQL connector, connection pooling, query handling
- **What works:** Complete database connectivity layer

---

## [COMPLETED] ✅ Phase 2: Core Infrastructure (DONE!)
**Goal:** Core system components and module system

### 2.1 ✅ Module Registry System
- **File:** `inphms/modules/registry.py` - **Status:** ✅ COMPLETED
- **Features:** Model registry, database per registry instance
- **What works:** Module loading and model registration framework

### 2.2 ✅ Service Layer
- **File:** `inphms/service/server.py` - **Status:** ✅ COMPLETED
- **Features:** HTTP server, worker processes, cron processing
- **What works:** Complete server infrastructure with workers

### 2.3 ✅ Addon System
- **File:** `inphms/addons/__init__.py` - **Status:** ✅ COMPLETED
- **Features:** Addon discovery, path management
- **What works:** Addon loading and management

### 2.4 ✅ Base Module Structure
- **File:** `inphms/addons/base/` - **Status:** ✅ COMPLETED
- **Features:** Base module with controllers
- **What works:** Basic module structure

---

## [WIP] Phase 3: Web Server Implementation (IN PROGRESS)
**Goal:** Create actual HTTP server using Werkzeug

### 3.1 ✅ HTTP Application Class
- **File:** `inphms/http.py` - **Status:** ✅ COMPLETED
- **Features:** WSGI application class defined
- **What works:** Basic HTTP application structure

### 3.2 ❌ Missing: Request Routing
- **File:** `inphms/http.py` - **Status:** ❌ NOT STARTED
- **Problem:** Application class is empty, no routing implementation
- **Fix:** Implement request routing and controller dispatching
- **Priority:** HIGH

### 3.3 ❌ Missing: Controller System
- **File:** `inphms/http.py` - **Status:** ❌ NOT STARTED  
- **Problem:** No route decorator or controller base class
- **Fix:** Add `@route` decorator and `Controller` base class
- **Priority:** HIGH

### 3.4 ❌ Missing: Template System
- **File:** `inphms/web/templates/` - **Status:** ❌ NOT STARTED
- **Problem:** No Jinja2 template integration
- **Fix:** Add template rendering and base templates
- **Priority:** MEDIUM

---

## [BLOCKED] Phase 4: Database Models (BLOCKED)
**Goal:** Connect to MariaDB and create basic models

### 4.1 ❌ Missing: MariaDB Support
- **File:** `requirements.txt` - **Status:** ❌ NOT STARTED
- **Problem:** Only PostgreSQL support (psycopg2), no MariaDB
- **Fix:** Add `pymysql` or `mysqlclient` for MariaDB
- **Priority:** HIGH

### 4.2 ❌ Missing: Model System
- **File:** `inphms/models/` - **Status:** ❌ NOT STARTED
- **Problem:** No ORM model system
- **Fix:** Create basic model classes and field types
- **Priority:** HIGH

### 4.3 ❌ Missing: User Model
- **File:** `inphms/addons/base/models/user.py` - **Status:** ❌ NOT STARTED
- **Problem:** No user management system
- **Fix:** Create User model with authentication
- **Priority:** MEDIUM

---

## [NOT STARTED] Phase 5: ERP Modules (NOT STARTED)
**Goal:** Create actual business logic modules

### 5.1 ❌ Missing: Plantation Module
- **File:** `inphms/addons/plantation/` - **Status:** ❌ NOT STARTED
- **Problem:** No plantation-specific features
- **Fix:** Create plantation management models and controllers
- **Priority:** MEDIUM

### 5.2 ❌ Missing: Web Interface
- **File:** `addons/web/controllers/main.py` - **Status:** ❌ NOT STARTED
- **Problem:** Controller is deprecated and empty
- **Fix:** Implement actual web interface controllers
- **Priority:** MEDIUM

---

## [COMPLETED] ✅ Phase 6: Windows Installer (DONE!)
**Goal:** Create Windows installer with all dependencies

### 6.1 ✅ NSIS Installer
- **File:** `setup/win32/setup.nsi` - **Status:** ✅ COMPLETED
- **Features:** Complete Windows installer with NSIS
- **What works:** Python bundling, PostgreSQL download, Nginx download

### 6.2 ✅ Docker Build System
- **File:** `setup/package.dfwine` - **Status:** ✅ COMPLETED
- **Features:** Wine-based Windows package building
- **What works:** Automated installer creation

### 6.3 ✅ Package Management
- **File:** `setup/package.py` - **Status:** ✅ COMPLETED
- **Features:** Multi-platform package building
- **What works:** Windows, Linux, and source package creation

---

## [NOT STARTED] Phase 7: Executable Build (NOT STARTED)
**Goal:** Create single .exe file for client deployment

### 7.1 ❌ Missing: PyInstaller Setup
- **File:** `requirements.txt` - **Status:** ❌ NOT STARTED
- **Problem:** No PyInstaller dependency
- **Fix:** Add `pyinstaller` to requirements
- **Priority:** MEDIUM

### 7.2 ❌ Missing: Build Script
- **File:** `build_exe.bat` - **Status:** ❌ NOT STARTED
- **Problem:** No executable build process
- **Fix:** Create one-click executable creation script
- **Priority:** MEDIUM

---

## [NOT STARTED] Phase 8: Testing & Deployment (NOT STARTED)
**Goal:** Ensure everything works before client delivery

### 8.1 ❌ Missing: Unit Tests
- **File:** `inphms/tests/` - **Status:** ❌ NOT STARTED
- **Problem:** No test framework
- **Fix:** Create unit tests for core functionality
- **Priority:** LOW

### 8.2 ❌ Missing: Integration Tests
- **Goal:** Test complete ERP workflow
- **Status:** ❌ NOT STARTED
- **Priority:** LOW

---

## IMMEDIATE NEXT STEPS (Do These First):

1. **Fix HTTP routing** - Implement request routing in `inphms/http.py`
2. **Add MariaDB support** - Replace PostgreSQL with MariaDB in requirements
3. **Create basic web page** - Get simple HTTP response working
4. **Test server startup** - Verify server can start and respond to requests

---

## DEPENDENCIES TO ADD:
```bash
# Add to requirements.txt:
pymysql>=1.0.0  # For MariaDB connection
pyinstaller>=5.0.0  # For .exe creation
```

## FILES TO CREATE/FIX:
- **HIGH PRIORITY:** Complete `inphms/http.py` routing implementation
- **HIGH PRIORITY:** Add MariaDB support to `requirements.txt`
- **MEDIUM PRIORITY:** Create `inphms/models/` directory and basic models
- **MEDIUM PRIORITY:** Implement web controllers in `addons/web/`

## CURRENT BLOCKERS:
- **HTTP routing not implemented** - Server can't handle web requests
- **No MariaDB support** - Only PostgreSQL supported
- **No model system** - No ORM for database operations
- **Web interface empty** - Controllers are deprecated placeholders

## REMINDER: NETIFACES DEPENDENCY
**IMPORTANT:** The `netifaces` package in `setup/package.dfwine` downloads from `nightly.odoo.com` (Odoo's servers). You need to:
1. **Bundle the netifaces wheel locally** in your project
2. **Host it on your own server** instead of depending on Odoo
3. **Update the Dockerfile** to use your local copy

**Next Action:** Start with Phase 3.2 - Implement HTTP request routing in `inphms/http.py`
