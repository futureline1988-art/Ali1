# نظام إدارة الحضور والانصراف (Enterprise Attendance Management System)

نظام سطح مكتب متعدد الشركات (Multi-Company / Multi-Tenant) لإدارة حضور وانصراف الموظفين، مبني بلغة Python باستخدام PySide6 (Qt6) وSQLAlchemy، مع واجهة عربية RTL افتراضية ودعم كامل للأجهزة البيومترية (ZKTeco وHikvision).

Built for organizations that need to manage attendance across an unlimited number of independent companies from a single installation, with full data isolation between tenants, biometric device integration, and Arabic-first bilingual reporting.

---

## المحتويات / Contents

- [نظرة عامة / Overview](#نظرة-عامة--overview)
- [الميزات الرئيسية / Key Features](#الميزات-الرئيسية--key-features)
- [البنية المعمارية / Architecture](#البنية-المعمارية--architecture)
- [حزمة التقنيات / Tech Stack](#حزمة-التقنيات--tech-stack)
- [هيكل المشروع / Project Structure](#هيكل-المشروع--project-structure)
- [التثبيت والتشغيل / Installation & Running](#التثبيت-والتشغيل--installation--running)
- [متغيرات البيئة / Environment Variables](#متغيرات-البيئة--environment-variables)
- [أول تشغيل / First Run](#أول-تشغيل--first-run)
- [نظام الترخيص / Licensing System](#نظام-الترخيص--licensing-system)
- [النسخ الاحتياطي / Backup & Restore](#النسخ-الاحتياطي--backup--restore)
- [الأجهزة البيومترية المدعومة / Supported Devices](#الأجهزة-البيومترية-المدعومة--supported-devices)
- [الاختبار / Testing](#الاختبار--testing)
- [الترخيص / License](#الترخيص--license)

---

## نظرة عامة / Overview

يوفّر النظام إدارة كاملة لدورة حياة الحضور: من تسجيل الموظفين وأجهزة البصمة، مرورًا بحساب الحضور اليومي (تأخير، انصراف مبكر، وقت إضافي) آليًا من بصمات الأجهزة أو الإدخال اليدوي، وصولًا إلى تقارير جاهزة للتصدير بصيغ Excel وPDF وCSV.

كل شركة مسجلة في النظام معزولة بالكامل عن غيرها: بياناتها، مستخدموها، أدوارها، أقسامها، موظفوها، أجهزتها، وإعداداتها لا تُشارَك ولا تتقاطع أبدًا مع شركة أخرى، رغم أنها تعمل جميعًا فوق قاعدة بيانات واحدة — إضافة شركة جديدة لا تتطلب أي تعديل في الكود.

The system covers the full attendance lifecycle: enrolling employees and biometric devices, automatically computing daily attendance (late arrivals, early leaves, overtime) from device punches or manual entry, and exporting ready-to-share reports in Excel, PDF, and CSV. Every company is fully data-isolated from every other, even though they all run on one shared database — onboarding a new tenant requires no code changes.

## الميزات الرئيسية / Key Features

- **تعدد الشركات (Multi-Tenant)**: عزل كامل للبيانات بين الشركات عبر `company_id` على مستوى طبقة المستودعات (Repository layer)، مع أدوار (Roles) خاصة بكل شركة.
- **الصلاحيات (RBAC)**: تحقق فعلي من الصلاحيات وقت التشغيل على كل عملية وكل شاشة (وليس فقط قائمة صلاحيات معروضة) — كل من التطبيق المكتبي وواجهة REST يتشاركان نفس مصدر الصلاحية.
- **لوحة تحكم**: إحصائيات لحظية — الموظفون النشطون، الأقسام، حالة الأجهزة، وملخص حضور اليوم (حاضر/متأخر/غائب/إجازة) — مع مخططات بيانية تنفيذية (اتجاه الحضور خلال 14 يومًا، وتوزيع الموظفين حسب القسم).
- **إدارة الموظفين والأقسام والفروع**: بحث، تصنيف هرمي للأقسام (شجرة قابلة لإعادة الترتيب)، إدارة فروع الشركة (المواقع الفعلية)، وتوليد رمز QR وباركود تلقائي لكل موظف.
- **الورديات والعطلات والإجازات**: تعريف ورديات العمل وتعيينها للموظفين، تقويم العطلات الرسمية، وطلبات إجازة قابلة للاعتماد — كلها تُحتسب تلقائيًا ضمن حالة الحضور اليومية.
- **الحضور والانصراف**: استيراد بصمات من الأجهزة، إدخال يدوي، واحتساب آلي لحالة اليوم (حاضر/متأخر/غياب/إجازة/عطلة) بالتوقيت المحلي للشركة.
- **الأجهزة البيومترية**: دعم بروتوكولات ZKTeco (TCP/UDP) وHikvision (ISAPI)، اختبار اتصال، مزامنة سجلات (يدويًا أو تلقائيًا حسب جدول)، ودفع بيانات الموظفين إلى الجهاز.
- **المهام المجدولة**: مزامنة تلقائية للأجهزة ونسخ احتياطي تلقائي للقاعدة، تعمل في الخلفية دون تدخل المستخدم (`SchedulerService`).
- **التقارير**: 6 أنواع تقارير (ملخص حضور، حسب الموظف، حسب القسم، المتأخرون، الوقت الإضافي، الغياب) بثلاث صيغ تصدير، مع دعم كامل للنص العربي RTL في ملفات PDF.
- **المستخدمون والصلاحيات**: أدوار قابلة للتخصيص لكل شركة، صلاحيات دقيقة (Permission catalog)، تدقيق كامل (Audit Log) لكل عملية حساسة.
- **الإعدادات**: ملف تعريف الشركة، تفضيلات العرض (المنطقة الزمنية، صيغة التاريخ، العملة)، ونسخ احتياطي/استعادة آمن لقاعدة البيانات (متوافق مع وضع WAL، ومشفّر).
- **واجهة عربية أولًا**: RTL افتراضي، تنسيق تاريخ `DD/MM/YYYY`، نظام 24 ساعة، أرقام عربية-هندية اختيارية، وسمة بصرية (Theme) فاتحة/داكنة مستوحاة من Fluent Design.
- **أمان**: كلمات مرور مُجزّأة (bcrypt)، تشفير الحقول الحساسة في قاعدة البيانات (Fernet)، قفل الحساب بعد محاولات فاشلة متكررة، انتهاء الجلسة عند عدم النشاط، وسجل تدقيق شامل.
- **واجهة REST اختيارية**: عملية منفصلة (`run_api.py`، مبنية على FastAPI) تعرض نفس بيانات الشركات عبر HTTP لأغراض التكامل، معطّلة افتراضيًا.

## البنية المعمارية / Architecture

النظام مبني وفق مبادئ **Clean Architecture** مع **Repository Pattern** وفصل صارم بين الطبقات:

```
┌─────────────────────────────────────────────────────────┐
│  ui/            PySide6 windows/pages — Qt signals only  │
├─────────────────────────────────────────────────────────┤
│  controllers/   Session-per-operation, ORM → dict          │
├─────────────────────────────────────────────────────────┤
│  services/      Business logic, validation, audit trail   │
├─────────────────────────────────────────────────────────┤
│  repositories/  Data access — the tenant-isolation point   │
├─────────────────────────────────────────────────────────┤
│  models/        SQLAlchemy ORM (declarative, typed)       │
└─────────────────────────────────────────────────────────┘
        ↕                              ↕
   database/ (SQLAlchemy engine)   devices/ (ZKTeco/Hikvision)
```

- **العزل بين الشركات (Tenant Isolation)**: `CompanyScopedRepository` هو نقطة الإنفاذ الوحيدة — أي استعلام عبر مستودع مُقيّد بشركة يضيف `WHERE company_id = ...` تلقائيًا، ولا يمكن لخدمة أن "تنسى" هذا الشرط.
- **نمط التحكم (Controllers)**: كل عملية تُنفَّذ داخل جلسة قاعدة بيانات مستقلة (`session_scope()`)، وتُحوَّل كائنات ORM إلى `dict` قبل إغلاق الجلسة — لا تلمس الواجهة أبدًا كائن SQLAlchemy قد يصبح منفصلاً (Detached).
- **الحذف الناعم (Soft Delete)** و**القفل التفاؤلي (Optimistic Locking)** و**معرّفات UUID عامة** مطبّقة عبر Mixins مشتركة في `models/base.py`.

## حزمة التقنيات / Tech Stack

| الطبقة | التقنية |
|---|---|
| الواجهة | PySide6 (Qt6) |
| قاعدة البيانات / ORM | SQLAlchemy 2.x (SQLite افتراضيًا، PostgreSQL/MySQL جاهزان عبر `DB_DIALECT`) |
| الأمان | bcrypt، HMAC-SHA256 |
| السجلات | Loguru |
| التقارير | openpyxl (Excel)، ReportLab (PDF)، CSV built-in |
| النص العربي في PDF | arabic-reshaper + python-bidi |
| رموز QR/الباركود | qrcode، python-barcode |
| الأجهزة البيومترية | pyzk (ZKTeco)، requests (Hikvision ISAPI) |
| الإعدادات | python-dotenv |

## هيكل المشروع / Project Structure

```
attendance_system/
├── main.py                # نقطة الدخول للتطبيق المكتبي (Composition Root)
├── run_api.py               # نقطة دخول منفصلة واختيارية لواجهة REST
├── config.py               # الإعدادات المركزية (مصدر واحد للحقيقة)
├── requirements.txt
├── database/
│   └── database.py         # محرك SQLAlchemy وإدارة الجلسات
├── alembic/                 # ترحيلات مخطط قاعدة البيانات
├── models/                 # نماذج ORM (18 نموذجًا)
├── repositories/           # طبقة الوصول للبيانات (نقطة عزل الشركات)
├── services/                # منطق الأعمال والتحقق والتدقيق والجدولة
├── devices/                 # موصلات الأجهزة البيومترية (ZKTeco/Hikvision)
├── controllers/              # جسر الواجهة ↔ الخدمات (إشارات Qt، RBAC)
├── ui/                      # نوافذ وشاشات PySide6
│   ├── theme.py             # السمة البصرية (فاتح/داكن)
│   ├── widgets.py            # عناصر مشتركة
│   ├── login_window.py
│   ├── main_window.py
│   └── ...                  # لوحة التحكم، الموظفون، الحضور، الفروع، إلخ.
├── api/                      # واجهة REST الاختيارية (FastAPI) - انظر run_api.py
├── utils/                    # i18n، الأمان، التشفير، السجلات، QR/الباركود، التصدير
├── tests/                    # حزمة pytest (منطق الأعمال الحرج)
└── assets/                   # الخطوط والأيقونات والترجمات
```

## التثبيت والتشغيل / Installation & Running

### المتطلبات / Prerequisites

- Python 3.13+
- (اختياري) PostgreSQL أو MySQL إن أردت تجاوز SQLite الافتراضي

### خطوات التثبيت / Setup

```bash
cd attendance_system

# إنشاء بيئة افتراضية / Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# تثبيت الاعتماديات / Install dependencies
pip install -r requirements.txt

# (اختياري) نسخ ملف الإعدادات / Optionally configure via .env
cp .env.example .env            # القيم الافتراضية كافية للتشغيل المحلي دون أي تعديل

# تشغيل التطبيق / Run the application
python main.py
```

عند أول تشغيل، يقوم `main.py` تلقائيًا بـ:
1. إنشاء مجلدات التشغيل (`data/`, `logs/`, إلخ).
2. تهيئة قاعدة البيانات وإنشاء جميع الجداول.
3. تهيئة قائمة الصلاحيات العامة (Permission catalog) إن لم تكن موجودة.
4. عرض شاشة تسجيل الدخول.

## متغيرات البيئة / Environment Variables

جميع المتغيرات اختيارية ولها قيم افتراضية معقولة للتطوير المحلي (`config.py` هو مصدر الحقيقة الكامل).

| المتغير | الافتراضي | الوصف |
|---|---|---|
| `APP_ENVIRONMENT` | `production` | `development` \| `testing` \| `production` |
| `APP_SECRET_KEY` | ⚠️ insecure placeholder | **يجب تغييره في الإنتاج.** يوقّع رموز الجلسة وواجهة REST. ولّد قيمة حقيقية بـ `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DB_DIALECT` | `sqlite` | `sqlite` \| `postgresql` \| `mysql` |
| `DB_SQLITE_PATH` | `data/attendance.db` | مسار ملف SQLite |
| `DB_HOST` / `DB_PORT` / `DB_USERNAME` / `DB_PASSWORD` / `DB_NAME` | — | إعدادات PostgreSQL/MySQL |
| `SECURITY_BCRYPT_ROUNDS` | `12` | تكلفة تجزئة كلمة المرور |
| `SECURITY_SESSION_TIMEOUT_MINUTES` | `30` | مهلة الخمول قبل انتهاء الجلسة |
| `SECURITY_MAX_LOGIN_ATTEMPTS` | `5` | محاولات الدخول قبل القفل |
| `SECURITY_LOGIN_LOCKOUT_MINUTES` | `15` | مدة قفل الحساب |
| `UI_DEFAULT_THEME` | `light` | `light` \| `dark` |
| `BACKUP_AUTO_ENABLED` / `BACKUP_INTERVAL_HOURS` / `BACKUP_RETENTION_COUNT` | `true` / `24` / `14` | سياسة النسخ الاحتياطي التلقائي (منفذة عبر `SchedulerService`) |
| `DEVICE_AUTO_SYNC_ENABLED` / `DEVICE_AUTO_SYNC_INTERVAL_MINUTES` | `true` / `15` | مزامنة الأجهزة التلقائية المجدولة |
| `DEVICE_ZKTECO_PORT` / `DEVICE_HIKVISION_PORT` | `4370` / `80` | المنافذ الافتراضية للأجهزة |
| `LOG_LEVEL` | `INFO` | مستوى التسجيل |
| `API_ENABLED` | `false` | تفعيل واجهة REST الاختيارية (عملية منفصلة، شغّلها بـ `python run_api.py`) |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | عنوان ومنفذ واجهة REST |
| `API_TOKEN_EXPIRES_MINUTES` | `480` | مدة صلاحية رمز الدخول لواجهة REST |

## أول تشغيل / First Run

لا توجد شركة أو مستخدم افتراضي مُجهَّز مسبقًا؛ يبدأ النظام بقائمة شركات فارغة. لإنشاء أول شركة ومستخدم مدير يمكن استخدام واجهة برمجية مباشرة (حتى تتوفر شاشة "تسجيل شركة جديدة" في نسخة لاحقة):

```python
from database.database import get_database
get_database().initialize()

with get_database().session_scope() as session:
    from services.company_service import CompanyService
    from services.user_service import UserService
    from repositories.role_repository import RoleRepository
    from models.enums import UserRole

    company = CompanyService(session).create_company(name="اسم الشركة")
    admin_role = next(
        r for r in RoleRepository(session, company_id=company.id).list_all()
        if r.code == UserRole.SYSTEM_ADMIN.value
    )
    UserService(session, company_id=company.id).create_user(
        username="admin",
        full_name="مدير النظام",
        password="ChangeMe123!",
        role_id=admin_role.id,
    )
```

بعدها يمكن تسجيل الدخول من التطبيق باسم المستخدم وكلمة المرور أعلاه، واختيار الشركة من قائمة تسجيل الدخول.

## نظام الترخيص / Licensing System

النظام محمي بترخيص مرتبط بالجهاز (Machine-Locked License)، مستقل تمامًا عن قاعدة البيانات وعن نظام تراخيص الشركات ضمن التطبيق (`models/license.py`، وهو خاص بحدود اشتراك كل شركة داخل النظام، لا بحماية التطبيق نفسه). يُتحقق من الترخيص عند كل إقلاع للتطبيق، قبل شاشة تسجيل الدخول.

The application is protected by a machine-locked license, fully independent of the database and of the in-app, per-company `models/license.py` (which governs each tenant's subscription limits, not the application's own protection). It is verified on every startup, before the login screen.

### كيف يعمل / How it works

- **معرّف الجهاز (Machine ID)**: بصمة SHA-256 مشتقة من اسم الجهاز والنظام والعنوان الفعلي للشبكة، تُعرض في شاشة التفعيل لإرسالها إلى المورّد.
- **مفتاح الترخيص**: نص موقّع رقميًا بخوارزمية Ed25519 (`AMS1.<payload>.<signature>`)، يحدد نوع الترخيص (تجريبي/شهري/سنوي/دائم)، تاريخ الانتهاء، وربطًا اختياريًا بجهاز محدد. يتحقق التطبيق من التوقيع باستخدام مفتاح عام مضمّن في الكود فقط — لا يحتاج اتصالًا بالإنترنت إطلاقًا.
- **التخزين المحلي**: الترخيص المُفعَّل يُخزَّن مشفّرًا (Fernet) بمفتاح مشتق من معرّف الجهاز نفسه، في `data/license.dat` — نسخ هذا الملف إلى جهاز آخر ينتج ملفًا لا يمكن فك تشفيره هناك.
- **النسخة التجريبية**: 14 يومًا، تُفعَّل ذاتيًا من داخل التطبيق دون الحاجة لمفتاح من المورّد، ومرة واحدة فقط لكل جهاز.

### إدارة الترخيص من داخل التطبيق / In-App License Management

بعد تسجيل الدخول، من **الإعدادات → الترخيص** (تبويب جديد ضمن شاشة الإعدادات الحالية، دون أي تعديل على بقية تبويباتها): عرض تفصيلي لحالة الترخيص (اسم الشركة، اسم العميل، النوع، الحالة، تاريخ التفعيل، تاريخ الانتهاء، معرّف الجهاز، معرّف الترخيص) مع خمسة أزرار إدارة:

- **تفعيل ترخيص**: لصق مفتاح جديد يحل محل الحالي.
- **تجديد الترخيص**: مخصص للاشتراكات الشهرية والسنوية فقط (بما فيها المنتهية) — يرفض مفاتيح التجديد من نوع تجريبي أو دائم، ويُظهر تاريخ الانتهاء الجديد فورًا.
- **تصدير طلب الترخيص**: يحفظ ملف JSON يحتوي معرّف الترخيص، اسم الشركة والعميل، معرّف الجهاز، وتفاصيل التفعيل — مع تضمين مفتاح الترخيص الموقّع الأصلي كاملاً، وهو ما يجعل الملف قابلاً للتحقق بشكل مستقل من قِبل المورّد (أو أي طرف يملك المفتاح العام) دون الحاجة لأي مفتاح توقيع إضافي داخل التطبيق.
- **إلغاء تفعيل الترخيص**: يمسح الترخيص من هذا الجهاز فورًا، مع خيار حفظ ملف طلب النقل في نفس الخطوة. يرسل العميل هذا الملف إلى المورّد، الذي يصدر بعدها مفتاحًا جديدًا لجهاز مختلف.
- **نسخ معرّف الجهاز**.

**ملاحظة مهمة حول نقل التراخيص**: بما أن النظام يعمل بالكامل دون اتصال بالإنترنت، فإن منع تفعيل نفس الترخيص على جهازين في آن واحد هو إجراء تنظيمي (المورّد يراجع ملف طلب النقل قبل إصدار مفتاح جديد) وليس ضمانًا تشفيريًا فوريًا — تحقيق ذلك بشكل آلي يتطلب خادم ترخيص مركزي، وهو بالضبط ما تم تجهيز البنية له مسبقًا (انظر أدناه) دون الحاجة لأي تعديل على واجهات الترخيص الحالية.

Post-login, from **Settings → License** (a new tab on the existing Settings screen — no other tab was touched): a full status view plus five actions — **Activate**, **Renew** (Monthly/Yearly only, including already-expired ones; rejects Trial/Lifetime renewal keys and shows the new expiry immediately), **Export License Request** (a JSON file embedding the original signed key itself as independently-verifiable proof — no extra signing key needed in the app), **Deactivate** (clears this machine immediately, optionally exporting a transfer request in the same step for the vendor to review before issuing a replacement key elsewhere), and **Copy Machine ID**. Preventing simultaneous activation on two machines is, honestly, a procedural control here (vendor reviews the transfer request) rather than a real-time cryptographic guarantee — that needs the online-backend extension point described below, with zero changes required to these screens when it's added.

### إصدار مفاتيح الترخيص (للمورّد فقط) / Issuing License Keys (Vendor-Only)

أداة سطر أوامر منفصلة تمامًا عن التطبيق، لا تُستدعى منه أبدًا:

```bash
# مرة واحدة فقط: توليد زوج مفاتيح التوقيع (احتفظ بالمفتاح الخاص بمكان آمن خارج المستودع)
python -m licensing.license_generator generate-keypair \
    --private-key-out /secure/location/private_key.pem \
    --public-key-out /tmp/public_key.pem
# ثم انسخ محتوى المفتاح العام إلى licensing/keys.py -> PUBLIC_KEY_PEM

# إصدار مفتاح سنوي مرتبط بجهاز عميل محدد (--company اختياري)
python -m licensing.license_generator issue \
    --private-key /secure/location/private_key.pem \
    --customer "اسم العميل" --company "اسم الشركة" \
    --type yearly \
    --machine-id <Machine-ID-الذي-أرسله-العميل>

# إصدار مفتاح دائم غير مرتبط بجهاز (يُفعَّل على أول جهاز يستخدمه)
python -m licensing.license_generator issue \
    --private-key /secure/location/private_key.pem \
    --customer "اسم العميل" \
    --type lifetime
```

**تحذير أمني**: `licensing/vendor/private_key.pem` هو المفتاح الخاص الذي يسمح بإصدار تراخيص صالحة لهذا التطبيق. لا يجوز مطلقًا تضمينه في التطبيق المُوزَّع أو رفعه إلى نظام التحكم بالإصدارات (مستثنى فعليًا عبر `.gitignore`). فقدانه يعني عدم القدرة على إصدار تراخيص جديدة لنفس المفتاح العام المضمّن حاليًا؛ تسريبه يعني إمكانية تزوير تراخيص من قبل أي طرف يحصل عليه.

### التوسع لخادم ترخيص عبر الإنترنت لاحقًا / Extending to an Online License Server

طبقة التحقق مبنية خلف واجهة `LicenseBackend` بسيطة (`verify(key) -> LicensePayload`) في `licensing/license_service.py`. التنفيذ الحالي (`LocalLicenseBackend`) يعمل بالكامل دون اتصال بالإنترنت. لإضافة تحقق عبر خادم لاحقًا (مثل التأكد من عدم إلغاء الاشتراك)، يكفي إنشاء صنف جديد يحقق نفس الواجهة وتمريره إلى `LicenseService(backend=...)` — دون أي تعديل على واجهة التفعيل أو التخزين المحلي أو بقية التطبيق.

## النسخ الاحتياطي / Backup & Restore

من شاشة **الإعدادات → النسخ الاحتياطي**: إنشاء نسخة فورية، استعراض النسخ السابقة، والاستعادة منها. الآلية تستخدم واجهة `sqlite3.Connection.backup()` الآمنة مع وضع WAL، وليس نسخًا خام للملف — يضمن ذلك عدم فقدان بيانات لم تُكتب بعد إلى القرص. **تنبيه**: الاستعادة تستبدل جميع البيانات الحالية بشكل نهائي.

## الأجهزة البيومترية المدعومة / Supported Devices

| البروتوكول | الاتصال | الحقل المطلوب |
|---|---|---|
| ZKTeco TCP/IP | TCP، المنفذ 4370 افتراضيًا | كلمة اتصال الجهاز (اختياري) |
| ZKTeco UDP | UDP، المنفذ 4370 افتراضيًا | كلمة اتصال الجهاز (اختياري) |
| Hikvision (ISAPI) | HTTP Digest Auth، المنفذ 80 افتراضيًا | `اسم_المستخدم:كلمة_المرور` |

مطابقة سجلات الجهاز بالموظفين تتم عبر `الرقم الوظيفي` (Employee Number)، ومزامنة السجلات آمنة عند التكرار (لا تُنشئ بصمات مكررة عند إعادة المزامنة).

## الاختبار / Testing

مجموعة اختبارات pytest حقيقية موجودة في `tests/` (انظر `pytest.ini`) — كل اختبار يشغّل قاعدة بيانات SQLite معزولة وحقيقية خاصة به (لا محاكاة/mock لطبقة ORM)، وتغطي احتساب الحضور، صلاحيات RBAC، التشفير، الفروع، المهام المجدولة، لوحة التحكم، وواجهة REST كاملة عبر `TestClient`.

```bash
pip install -r requirements.txt   # يشمل pytest وpytest-qt وhttpx
pytest
```

كما تم التحقق يدويًا من كل شاشة واجهة أثناء التطوير عبر سيناريوهات تشغيل حقيقية ضد `QApplication` حقيقي. لتشغيل التطبيق في بيئة بلا شاشة (headless):

```bash
QT_QPA_PLATFORM=offscreen python main.py
```

## الترخيص / License

هذا مشروع داخلي مخصص. الخطوط المرفقة (`assets/fonts/DejaVuSans*.ttf`) مرخّصة بموجب [Bitstream Vera License](assets/fonts/LICENSE.txt) وتسمح بالاستخدام والتوزيع التجاري.
