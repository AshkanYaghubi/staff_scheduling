from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
import math
import os
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.postgresql import JSONB
from datetime import time as dt_time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'AudentesFortunaIuvat'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://root:oynn9r0O2KJ60NeTwYtzcHNK@rainier.liara.cloud:32419/backup'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ================= DB MODELS =================

class BranchInvoiceStats(db.Model):
    __tablename__ = 'branch_invoice_stats'
    __table_args__ = {'schema': 'schedule'}

    branchname = db.Column(db.String(100), primary_key=True)
    invoicedate = db.Column(db.Date, primary_key=True)
    invoicehour = db.Column(db.Integer, primary_key=True)
    totalminutesinhour = db.Column(db.Float)
    totalinvoices = db.Column(db.Integer)
    totalitems = db.Column(db.Float)

class Branch(db.Model):
    __tablename__ = 'branches'
    __table_args__ = {'schema': 'core'}

    id = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String(100))
    branch_name_farsi = db.Column(db.String(100))
    is_active = db.Column(db.Boolean)
    branch_area = db.Column(db.String(50))
    branch_code_mupra = db.Column(db.String(50))
    branch_status = db.Column(db.String(50))
    opening_hour = db.Column(db.Time)
    closing_hour = db.Column(db.Time)
    branch_minimum_staff = db.Column(db.Integer)
    morning_shift_start = db.Column(db.Time)
    morning_shift_end = db.Column(db.Time)
    evening_shift_start = db.Column(db.Time)
    evening_shift_end = db.Column(db.Time)
    # NEW: Notes box for branch editors and viewers
    notes = db.Column(db.Text)

class EmployeeAuth(db.Model):
    __tablename__ = 'employee_auth'
    __table_args__ = {'schema': 'core'}
    employee_number = db.Column(db.String(50), primary_key=True)
    password_hash = db.Column(db.String(255))

class CoreEmployee(db.Model):
    __tablename__ = 'employee_identity'
    __table_args__ = {'schema': 'core'}
    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(50), unique=True)
    full_name = db.Column(db.String(255))
    department_id = db.Column(db.Integer)
    job_title = db.Column(db.String(100))
    is_active = db.Column(db.Boolean)
    user_type = db.Column(db.String(100))
    mobile_phone = db.Column(db.String(50))

class Department(db.Model):
    __tablename__ = 'departments'
    __table_args__ = {'schema': 'core'}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))

class AppAdmin(db.Model):
    __tablename__ = 'app_admins'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # NEW: Role identifier to support branch_editor and branch_viewer admin-like logins
    role = db.Column(db.String(20), default='admin', nullable=False)

class AuthEmployee(db.Model):
    __tablename__ = 'auth_employees'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(255), nullable=False)
    department_id = db.Column(db.Integer)
    password_text = db.Column(db.String(255))

class BranchSupervisorPassword(db.Model):
    __tablename__ = 'branch_supervisor_passwords'
    __table_args__ = {'schema': 'schedule'}
    department_id = db.Column(db.Integer, primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    password_text = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Schedule(db.Model):
    __tablename__ = 'schedules'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String(100))
    target_date = db.Column(db.Date)
    target_hour = db.Column(db.Integer)
    required_baristas = db.Column(db.Float)

class ShiftCategory(db.Model):
    """A named grouping of shifts (e.g. 'صبح', 'عصر', 'میانی').

    Categories let an admin bulk-shift every shift that belongs to the group at
    once (start later/earlier, end later/earlier, or move the whole block).
    branch_name = NULL means the category is shared across all branches.
    """
    __tablename__ = 'shift_categories'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#6366F1')
    branch_name = db.Column(db.String(100))   # NULL => available to every branch
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ShiftTemplate(db.Model):
    """A reusable weekly shift definition for one branch.

    Columns start_time/end_time are the BASE hours. day_config holds a per-day
    override for each of the seven days, keyed by short day name:

        {"sat": {"mode": "default"},
         "sun": {"mode": "default"},
         "mon": {"mode": "custom", "start": "10:00", "end": "18:00", "locked": true},
         "wed": {"mode": "off"},
         "thu": {"mode": "custom", "start": "09:00", "end": "14:00"},
         "fri": {"mode": "off"}}

    mode is one of:
        default -> use the base start_time/end_time
        custom  -> use this day's own start/end
        off     -> no shift generated for that day
    locked  -> the day was edited by hand, so Saturday no longer propagates to it
    """
    __tablename__ = 'shift_templates'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('schedule.shift_categories.id', ondelete='SET NULL'))
    day_config = db.Column(JSONB, default=dict)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OfficialShift(db.Model):
    __tablename__ = 'official_shifts'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='draft')
    assigned_employee_id = db.Column(db.String(50))
    assigned_employee_name = db.Column(db.String(255))
    supervisor_id = db.Column(db.String(50))
    supervisor_name = db.Column(db.String(255))
    category_id = db.Column(db.Integer, db.ForeignKey('schedule.shift_categories.id', ondelete='SET NULL'))

class DeletedSchedule(db.Model):
    __tablename__ = 'deleted_schedules'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    original_id = db.Column(db.Integer)
    branch_name = db.Column(db.String(100))
    target_date = db.Column(db.Date)
    target_hour = db.Column(db.Integer)
    required_baristas = db.Column(db.Float)
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow)

class DeletedOfficialShift(db.Model):
    __tablename__ = 'deleted_official_shifts'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    original_id = db.Column(db.Integer)
    branch_name = db.Column(db.String(100))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    status = db.Column(db.String(20))
    assigned_employee_id = db.Column(db.String(50))
    assigned_employee_name = db.Column(db.String(255))
    supervisor_id = db.Column(db.String(50))
    supervisor_name = db.Column(db.String(255))
    category_id = db.Column(db.Integer)
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow)

class SealHistory(db.Model):
    __tablename__ = 'seal_history'
    __table_args__ = {'schema': 'core'}
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    branch_name_farsi = db.Column(db.String(255), nullable=False)
    datetime = db.Column(db.DateTime, nullable=False)

class ShiftDeadline(db.Model):
    __tablename__ = 'shift_deadlines'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    deadline_datetime = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class WeeklyDeadline(db.Model):
    __tablename__ = 'weekly_deadlines'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    week_start_date = db.Column(db.Date, unique=True, nullable=False)
    deadline_datetime = db.Column(db.DateTime, nullable=False)

class GuestShiftHistory(db.Model):
    __tablename__ = 'guest_shift_history'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    barista_employee_number = db.Column(db.String(50))
    barista_name = db.Column(db.String(255))
    home_branch = db.Column(db.String(100))
    guest_branch = db.Column(db.String(100))
    shift_id = db.Column(db.Integer)
    shift_start_time = db.Column(db.DateTime)
    shift_end_time = db.Column(db.DateTime)
    finalized_at = db.Column(db.DateTime, default=datetime.utcnow)

class BranchWeekLock(db.Model):
    __tablename__ = 'branch_week_locks'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String(100))
    week_start_date = db.Column(db.Date)
    locked_at = db.Column(db.DateTime, default=datetime.utcnow)
    locked_by = db.Column(db.String(50))
    supervisor_note = db.Column(db.Text)

class ScheduleCoreEmployee(db.Model):
    __tablename__ = 'schedule_core_employees'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(50), unique=True)
    full_name = db.Column(db.String(255))
    department_id = db.Column(db.Integer)
    job_title = db.Column(db.String(100))
    is_active = db.Column(db.Boolean)
    user_type = db.Column(db.String(100))
    mobile_phone = db.Column(db.String(20))



# ================= UNDO / HISTORY TRACKING MODEL =================

class ActionLog(db.Model):
    __tablename__ = 'action_logs'
    __table_args__ = {'schema': 'schedule'}
    id = db.Column(db.Integer, primary_key=True)
    action_type = db.Column(db.String(50), nullable=False) # 'insert', 'update', 'delete'
    details = db.Column(db.JSON, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

def log_action(action_type, details):
    log_entry = ActionLog(action_type=action_type, details=details)
    db.session.add(log_entry)


# ================= SCHEMA UPGRADES (idempotent) =================

def ensure_schema_upgrades():
    """Add the shift-category table/columns to an already-populated database.

    Safe to run on every boot: every statement is guarded with IF NOT EXISTS.
    """
    statements = [
        """
        CREATE TABLE IF NOT EXISTS schedule.shift_categories (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(100) NOT NULL,
            color       VARCHAR(20) DEFAULT '#6366F1',
            branch_name VARCHAR(100),
            description TEXT,
            created_at  TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS schedule.shift_templates (
            id          SERIAL PRIMARY KEY,
            branch_name VARCHAR(100) NOT NULL,
            title       VARCHAR(100) NOT NULL,
            start_time  TIME NOT NULL,
            end_time    TIME NOT NULL,
            category_id INTEGER,
            day_config  JSONB DEFAULT '{}'::jsonb,
            sort_order  INTEGER DEFAULT 0,
            created_at  TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_shift_templates_branch
            ON schedule.shift_templates (branch_name)
        """,
        "ALTER TABLE schedule.branch_week_locks ADD COLUMN IF NOT EXISTS supervisor_note TEXT",
        "ALTER TABLE schedule.official_shifts ADD COLUMN IF NOT EXISTS category_id INTEGER",
        "ALTER TABLE schedule.deleted_official_shifts ADD COLUMN IF NOT EXISTS category_id INTEGER",
        """
        CREATE INDEX IF NOT EXISTS idx_official_shifts_category
            ON schedule.official_shifts (category_id)
        """,
    ]

    # The FK is added separately: if it already exists the statement raises, and
    # we do not want that to roll back the column creation above.
    fk_statement = """
        ALTER TABLE schedule.official_shifts
        ADD CONSTRAINT fk_official_shifts_category
        FOREIGN KEY (category_id) REFERENCES schedule.shift_categories (id)
        ON DELETE SET NULL
    """

    with db.engine.begin() as conn:
        for stmt in statements:
            conn.execute(db.text(stmt))

    try:
        with db.engine.begin() as conn:
            conn.execute(db.text(fk_statement))
    except Exception:
        pass  # constraint already present


try:
    with app.app_context():
        ensure_schema_upgrades()
except Exception as _e:  # never block boot on a migration hiccup
    print(f"[schema upgrade skipped] {_e}")

# ================= AUTH & ROLES =================
class AuthUser(UserMixin):
    def __init__(self, user_id, username, role, full_name, department_id=None, branch_name=None):
        self.id = user_id; self.username = username; self.role = role; self.full_name = full_name
        self.department_id = department_id; self.branch_name = branch_name
    def get_id(self): return self.id

@login_manager.user_loader
def load_user(user_id):
    if user_id.startswith('adm_'):
        admin = AppAdmin.query.filter_by(username=user_id[4:]).first()
        if admin: return AuthUser(user_id, admin.username, admin.role, 'Administrator')
    elif user_id.startswith('emp_'):
        auth_emp = AuthEmployee.query.filter_by(employee_number=user_id[4:]).first()
        if auth_emp:
            branch_name = None
            if auth_emp.role == 'supervisor' and auth_emp.department_id:
                dept = Department.query.get(auth_emp.department_id)
                if dept: branch_name = dept.name
            return AuthUser(user_id, auth_emp.employee_number, auth_emp.role, auth_emp.full_name, auth_emp.department_id, branch_name)
    return None

def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                flash('Access denied.')
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper

# ================= ROUTES =================
@app.context_processor
def inject_branches():
    if current_user.is_authenticated and current_user.role == 'supervisor':
        return dict(all_branches=[current_user.branch_name] if current_user.branch_name else [])
    b1 = [b[0] for b in db.session.query(Schedule.branch_name).distinct().all() if b[0]]
    b2 = [b[0] for b in db.session.query(OfficialShift.branch_name).distinct().all() if b[0]]
    return dict(all_branches=sorted(list(set(b1 + b2))))

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['identifier']
        password = request.form['password']
        admin = AppAdmin.query.filter_by(username=identifier).first()
        if admin and check_password_hash(admin.password_hash, password):
            # Pass admin.role ('admin', 'branch_editor', 'branch_viewer')
            login_user(AuthUser(f"adm_{admin.username}", admin.username, admin.role, 'Administrator'))
            if admin.role in ['branch_editor', 'branch_viewer']:
                return redirect(url_for('admin_manage_branches'))
            return redirect(url_for('admin_calendar'))
            
        auth_emp = AuthEmployee.query.filter_by(employee_number=identifier).first()
        if auth_emp and check_password_hash(auth_emp.password_hash, password):
            branch_name = None
            if auth_emp.role == 'supervisor' and auth_emp.department_id:
                dept = Department.query.get(auth_emp.department_id)
                if dept: branch_name = dept.name
            user_obj = AuthUser(f"emp_{identifier}", identifier, auth_emp.role, auth_emp.full_name, auth_emp.department_id, branch_name)
            login_user(user_obj)
            if auth_emp.role == 'supervisor':
                return redirect(url_for('supervisor_dashboard'))
            elif auth_emp.role == 'barista':
                flash('Barista dashboard not implemented yet.', 'info')
                return redirect(url_for('barista_dashboard'))
            else:
                flash('ACCESS DENIED!')
                return redirect(url_for('login'))
        flash('Invalid credentials')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/admin/generate', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_dashboard():
    if request.method == 'POST':
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
        old_schedules = Schedule.query.filter(Schedule.target_date >= start_date, Schedule.target_date <= end_date).all()
        archived_schedules = [
            DeletedSchedule(
                original_id=s.id, branch_name=s.branch_name,
                target_date=s.target_date, target_hour=s.target_hour,
                required_baristas=s.required_baristas
            ) for s in old_schedules
        ]
        if archived_schedules:
            db.session.bulk_save_objects(archived_schedules)
        Schedule.query.filter(Schedule.target_date >= start_date, Schedule.target_date <= end_date).delete()
        hist_start, hist_end = start_date - timedelta(days=14), end_date - timedelta(days=7)
        records = BranchInvoiceStats.query.filter(BranchInvoiceStats.invoicedate >= hist_start, BranchInvoiceStats.invoicedate <= hist_end).all()
        history_dict = {(r.branchname, r.invoicedate, r.invoicehour): r.totalminutesinhour for r in records}
        branches = {r.branchname for r in records}

        new_schedules = []
        curr = start_date
        while curr <= end_date:
            for branch in branches:
                for hr in range(24):
                    w1 = history_dict.get((branch, curr-timedelta(days=7), hr), 0)
                    w2 = history_dict.get((branch, curr-timedelta(days=14), hr), 0)
                    vals = [v for v in [w1, w2] if v > 0]
                    if vals:
                        avg_minutes = sum(vals) / len(vals)
                        new_schedules.append(Schedule(branch_name=branch, target_date=curr, target_hour=hr, required_baristas=round(avg_minutes/60, 1)))
            curr += timedelta(days=1)
        db.session.bulk_save_objects(new_schedules)
        db.session.commit()
        flash('Schedules safely generated and overlaps archived!', 'success')
        return redirect(url_for('admin_calendar'))
    return render_template('admin_dashboard.html')

@app.route('/admin/employees', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_manage_employees():
    # Store excluded numbers as strings to match DB data type
    EXCLUDED_EMPLOYEE_NUMBERS = {'1027', '3013'}
    EXCLUDED_JOB_TITLES = ['مدیر']
    
    if request.method == 'POST':
        staged_employees = ScheduleCoreEmployee.query.all()
        filtered_staged = []
        skipped_employees = []
        
        for staged in staged_employees:
            emp_num = str(staged.employee_number).strip() if staged.employee_number else ''
            if not emp_num:
                continue

            # 1. Skip excluded numbers
            if emp_num in EXCLUDED_EMPLOYEE_NUMBERS:
                skipped_employees.append(f"{staged.full_name} ({emp_num}) - Excluded by number")
                continue

            # 2. Skip managers
            job_title = (staged.job_title or '').strip()
            if any(excluded.lower() in job_title.lower() for excluded in EXCLUDED_JOB_TITLES):
                skipped_employees.append(f"{staged.full_name} ({job_title}) - Manager")
                continue

            # 3. Skip inactive employees
            if staged.is_active is False:
                skipped_employees.append(f"{staged.full_name} ({emp_num}) - Inactive")
                continue

            filtered_staged.append(staged)
        
        existing_auth_map = {str(e.employee_number).strip(): e for e in AuthEmployee.query.all()}
        branch_pws = {bp.department_id: bp for bp in BranchSupervisorPassword.query.all()}
        
        new_accounts = []
        updated_accounts = 0
        
        for staged in filtered_staged:
            emp_num = str(staged.employee_number).strip()
            job_title = (staged.job_title or '').strip()
            
            # Flexible role check: If 'سرپرست' is anywhere in job_title
            role = 'supervisor' if 'سرپرست' in job_title else 'barista'
            saved_branch_pw = branch_pws.get(staged.department_id) if role == 'supervisor' else None

            if emp_num in existing_auth_map:
                user = existing_auth_map[emp_num]
                user.full_name = staged.full_name
                user.role = role
                user.department_id = staged.department_id
                
                # Apply fixed branch supervisor password if role is supervisor
                if role == 'supervisor' and saved_branch_pw:
                    user.password_hash = saved_branch_pw.password_hash
                    user.password_text = saved_branch_pw.password_text

                updated_accounts += 1
            else:
                if role == 'supervisor' and saved_branch_pw:
                    hashed_pw = saved_branch_pw.password_hash
                    pw_text = saved_branch_pw.password_text
                else:
                    raw_pw = str(staged.mobile_phone).strip() if staged.mobile_phone else "Medici1230"
                    if len(raw_pw) < 4: 
                        raw_pw = "Medici1230"
                    hashed_pw = generate_password_hash(raw_pw)
                    pw_text = raw_pw if role == 'supervisor' else None

                new_accounts.append(AuthEmployee(
                    employee_number=emp_num,
                    full_name=staged.full_name,
                    password_hash=hashed_pw,
                    password_text=pw_text,
                    role=role,
                    department_id=staged.department_id
                ))
        
        if new_accounts:
            db.session.add_all(new_accounts)
        
        # ===== Handle fired / inactive employees =====
        staged_employee_numbers = {str(staged.employee_number).strip() for staged in filtered_staged}
        fired_employees = AuthEmployee.query.filter(
            AuthEmployee.role.in_(['barista', 'supervisor']),
            ~AuthEmployee.employee_number.in_(staged_employee_numbers)
        ).all()
        
        fired_count = 0
        for emp in fired_employees:
            if emp.department_id != 68:  # Department 68 = Fired / Inactive
                emp.department_id = 68
                fired_count += 1
        # ==============================================
        
        db.session.commit()
        
        success_msg = f'Sync complete! Created {len(new_accounts)} new accounts, updated {updated_accounts} existing accounts.'
        if fired_count > 0:
            success_msg += f' Moved {fired_count} inactive/fired employees to department 68.'
        if skipped_employees:
            success_msg += f' Skipped {len(skipped_employees)} employees (managers/inactive/excluded).'
            
        flash(success_msg, 'success')
        return redirect(url_for('admin_manage_employees'))

    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '', type=str)
    role_filter = request.args.get('role', '', type=str)
    department_filter = request.args.get('department', '', type=str)
    
    query = AuthEmployee.query
    if search:
        query = query.filter(
            db.or_(
                AuthEmployee.employee_number.ilike(f'%{search}%'),
                AuthEmployee.full_name.ilike(f'%{search}%'),
                AuthEmployee.role.ilike(f'%{search}%')
            )
        )
    if role_filter:
        query = query.filter(AuthEmployee.role == role_filter)
    if department_filter and department_filter.isdigit():
        query = query.filter(AuthEmployee.department_id == int(department_filter))
        
    query = query.order_by(AuthEmployee.employee_number)
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    auth_employees = pagination.items
    total_count = pagination.total
    department_names = {d.id: d.name for d in Department.query.all()}
    all_departments = Department.query.order_by(Department.name).all()
    
    return render_template('admin_manage_employees.html', 
                         auth_employees=auth_employees, 
                         department_names=department_names,
                         all_departments=all_departments,
                         pagination=pagination,
                         total_count=total_count,
                         current_search=search,
                         current_role_filter=role_filter,
                         current_department_filter=department_filter)

@app.route('/admin/branches')
@login_required
@role_required('admin', 'branch_editor', 'branch_viewer')
def admin_manage_branches():
    branches = Branch.query.filter(Branch.branch_status != 'discontinued').order_by(Branch.branch_name).all()
    all_departments = Department.query.all()
    
    # --- 1. Helper to extract a cleaned SET of words (Solves reversed words like 'ونک اکسپرس' vs 'اکسپرس ونک') ---
    def get_words(text):
        if not text: return set()
        t = text.lower().replace('ي', 'ی').replace('ك', 'ک').replace('آ', 'ا').replace('أ', 'ا').replace('‌', ' ')
        for char in ['-', '_', '.', '(', ')', '/', ',', '،']:
            t = t.replace(char, ' ')
        ignore_words = {'شعبه', 'کافه', 'فروشگاه', 'مجتمع', 'گروه'}
        return set(w for w in t.split() if w and w not in ignore_words)

    # --- 2. Helper to extract an absolute squished string (Solves missing spaces like 'ایران مال' vs 'ایرانمال') ---
    def get_squished(text):
        if not text: return ""
        t = text.lower().replace('ي', 'ی').replace('ك', 'ک').replace('آ', 'ا').replace('أ', 'ا').replace('‌', '')
        for char in ['-', '_', '.', '(', ')', '/', ',', '،', ' ']:
            t = t.replace(char, '')
        return t.replace('شعبه', '').replace('کافه', '').replace('فروشگاه', '')

    dept_info = []
    for d in all_departments:
        if d.name:
            dept_info.append({
                'id': d.id,
                'name': d.name,
                'words': get_words(d.name),
                'squished': get_squished(d.name)
            })

    # --- 3. Pull supervisors from BOTH tables to guarantee nobody is missed ---
    auth_sups = AuthEmployee.query.filter(AuthEmployee.role == 'supervisor', AuthEmployee.department_id != 68).all()
    core_sups = ScheduleCoreEmployee.query.filter(ScheduleCoreEmployee.job_title.ilike('%سرپرست%'), ScheduleCoreEmployee.is_active == True).all()
    
    dept_id_to_supervisors = {}
    def add_sup(dept_id, name):
        if dept_id and name:
            if dept_id not in dept_id_to_supervisors:
                dept_id_to_supervisors[dept_id] = set()
            dept_id_to_supervisors[dept_id].add(name.strip())
            
    for s in auth_sups:
        add_sup(s.department_id, s.full_name)
    for s in core_sups:
        add_sup(s.department_id, s.full_name)

    # --- 4. Deep Similarity Matching ---
    branch_supervisors = {}
    for branch in branches:
        display_key = branch.branch_name_farsi or branch.branch_name
        
        b_en_words = get_words(branch.branch_name)
        b_fa_words = get_words(branch.branch_name_farsi)
        b_en_squished = get_squished(branch.branch_name)
        b_fa_squished = get_squished(branch.branch_name_farsi)
        
        best_match_id = None
        best_score = 0.0
        
        for d in dept_info:
            # Phase A: Try exact squished match first
            if (b_en_squished and b_en_squished == d['squished']) or \
               (b_fa_squished and b_fa_squished == d['squished']):
                best_score = 1.0
                best_match_id = d['id']
                break
                
            # Phase B: Calculate word intersection overlap (Jaccard Similarity)
            d_words = d['words']
            
            score_fa = 0.0
            if b_fa_words and d_words:
                inter = len(b_fa_words.intersection(d_words))
                union = len(b_fa_words.union(d_words))
                score_fa = inter / union if union > 0 else 0.0
                
            score_en = 0.0
            if b_en_words and d_words:
                inter = len(b_en_words.intersection(d_words))
                union = len(b_en_words.union(d_words))
                score_en = inter / union if union > 0 else 0.0
            
            max_score = max(score_fa, score_en)
            
            # Keep the department with the highest overlap
            if max_score > best_score:
                best_score = max_score
                best_match_id = d['id']
                
        # Must have at least ~50% word overlap to prevent completely wrong branches matching
        if best_score >= 0.49 and best_match_id in dept_id_to_supervisors:
            branch_supervisors[display_key] = list(dept_id_to_supervisors[best_match_id])
        else:
            branch_supervisors[display_key] = []

    return render_template('admin_manage_branches.html', 
                         branches=branches, 
                         branch_supervisors=branch_supervisors,
                         user_role=current_user.role)

@app.route('/api/employees/<string:employee_number>/reset_password', methods=['PUT'])
@login_required
@role_required('admin')
def api_reset_employee_password(employee_number):
    auth_emp = AuthEmployee.query.filter_by(employee_number=employee_number).first_or_404()
    new_password = request.json.get('password')
    if not new_password: return jsonify({'error': 'Password is required'}), 400
    auth_emp.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
    db.session.commit()
    return jsonify({'success': True, 'message': f'Password reset successfully.'})

@app.route('/admin/core_data', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_core_data_management():
    ALLOWED_DEPT_IDS = {
        1, 2, 6, 7, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 24, 25, 29, 30, 31, 32, 34, 37, 38, 39, 40, 41, 
        42, 43, 45, 46, 47, 50, 52, 55, 56, 57, 59, 60, 61, 64, 65, 66, 67, 69, 70
    }
    EXCLUDED_JOB_TITLES = ['مدیر']
    if request.method == 'POST':
        try:
            db.session.query(ScheduleCoreEmployee).delete()
            master_employees = CoreEmployee.query.filter_by(is_active=True).all()
            replica_objs = []
            skipped_managers = []
            for emp in master_employees:
                try:
                    d_id = int(emp.department_id) if emp.department_id else 0
                except: 
                    d_id = 0
                if d_id not in ALLOWED_DEPT_IDS:
                    continue
                job_title_lower = (emp.job_title or '').lower()
                is_manager = any(excluded.lower() in job_title_lower for excluded in EXCLUDED_JOB_TITLES)
                if is_manager:
                    skipped_managers.append(f"{emp.full_name} ({emp.job_title})")
                    continue
                replica_objs.append(ScheduleCoreEmployee(
                    employee_number = emp.employee_number,
                    full_name = emp.full_name,
                    department_id = d_id,
                    job_title = emp.job_title,
                    is_active = emp.is_active,
                    user_type = emp.user_type,
                    mobile_phone = emp.mobile_phone
                ))
            if replica_objs:
                db.session.bulk_save_objects(replica_objs)
            db.session.commit()
            success_msg = f'Imported {len(replica_objs)} branch-only employees (non-managers).'
            if skipped_managers:
                success_msg += f' Skipped {len(skipped_managers)} managers.'
            flash(success_msg, 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_core_data_management'))

    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '', type=str)
    department_filter = request.args.get('department', '', type=str)
    query = db.session.query(ScheduleCoreEmployee, Department.name).outerjoin(Department, ScheduleCoreEmployee.department_id == Department.id)
    for excluded in EXCLUDED_JOB_TITLES:
        query = query.filter(~ScheduleCoreEmployee.job_title.ilike(f'%{excluded}%'))
    if search:
        query = query.filter(
            db.or_(
                ScheduleCoreEmployee.employee_number.ilike(f'%{search}%'),
                ScheduleCoreEmployee.full_name.ilike(f'%{search}%'),
                ScheduleCoreEmployee.job_title.ilike(f'%{search}%'),
                ScheduleCoreEmployee.mobile_phone.ilike(f'%{search}%')
            )
        )
    if department_filter and department_filter.isdigit():
        query = query.filter(ScheduleCoreEmployee.department_id == int(department_filter))
    query = query.order_by(ScheduleCoreEmployee.employee_number)
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    employees = pagination.items
    total_count = pagination.total
    all_depts = Department.query.order_by(Department.name).all()
    return render_template('admin_core_data.html', 
                         employees_data=employees, 
                         all_departments=all_depts,
                         pagination=pagination,
                         total_count=total_count,
                         current_search=search,
                         current_department_filter=department_filter)

@app.route('/api/schedule_core_employee/<int:id>/update_department', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_schedule_employee_department(id):
    employee = ScheduleCoreEmployee.query.get_or_404(id)
    data = request.json
    new_dept_id = data.get('department_id')
    if new_dept_id is None:
        return jsonify({'success': False, 'message': 'Department ID is required.'}), 400
    try:
        employee.department_id = int(new_dept_id)
        db.session.commit()
        new_dept = Department.query.get(int(new_dept_id))
        new_dept_name = new_dept.name if new_dept else 'N/A'
        return jsonify({
            'success': True,
            'message': f'Department for {employee.full_name} updated.',
            'new_department_name': new_dept_name,
            'new_department_id': new_dept_id
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/employees/<string:employee_number>/delete', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_auth_employee(employee_number):
    auth_emp = AuthEmployee.query.filter_by(employee_number=employee_number).first_or_404()
    db.session.delete(auth_emp)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/calendar')
@login_required
@role_required('admin')
def admin_calendar(): return render_template('calendar.html', view_type='admin')

@app.route('/supervisor')
@login_required
@role_required('supervisor')
def supervisor_dashboard():
    return render_template('supervisor_schedule.html', view_type='supervisor')

@app.route('/admin/schedule')
@login_required
@role_required('admin')
def admin_schedule():
    return render_template('supervisor_schedule.html', view_type='admin')

@app.route('/admin/supervisors')
@login_required
@role_required('admin')
def admin_manage_supervisors():
    # Filter out department 68 and any department containing 'منفک'
    supervisors = db.session.query(AuthEmployee).outerjoin(
        Department, AuthEmployee.department_id == Department.id
    ).filter(
        AuthEmployee.role == 'supervisor',
        AuthEmployee.department_id != 68,
        db.or_(Department.name.is_(None), ~Department.name.ilike('%منفک%'))
    ).order_by(AuthEmployee.full_name).all()

    branch_pws = {bp.department_id: bp for bp in BranchSupervisorPassword.query.all()}
    
    dirty = False
    for sup in supervisors:
        if sup.department_id in branch_pws:
            bp = branch_pws[sup.department_id]
            if sup.password_text != bp.password_text:
                sup.password_hash = bp.password_hash
                sup.password_text = bp.password_text
                dirty = True
        elif sup.department_id and sup.password_text:
            bp = BranchSupervisorPassword(
                department_id=sup.department_id,
                password_hash=sup.password_hash,
                password_text=sup.password_text
            )
            db.session.add(bp)
            branch_pws[sup.department_id] = bp
            dirty = True
            
    if dirty:
        db.session.commit()

    departments = {d.id: d.name for d in Department.query.all()}
    return render_template('admin_supervisors.html', supervisors=supervisors, departments=departments)

@app.route('/api/supervisors/<int:id>/update_password', methods=['POST'])
@login_required
@role_required('admin')
def api_update_supervisor_password(id):
    emp = AuthEmployee.query.get_or_404(id)
    if emp.role != 'supervisor':
        return jsonify({'success': False, 'error': 'Account is not a supervisor.'}), 400
        
    data = request.json
    new_password = data.get('password', '').strip()
    
    if not new_password:
        return jsonify({'success': False, 'error': 'Password cannot be blank.'}), 400
        
    hashed_pw = generate_password_hash(new_password, method='scrypt')
    emp.password_hash = hashed_pw
    emp.password_text = new_password

    # Lock fixed password to this branch/department
    if emp.department_id:
        branch_pw = BranchSupervisorPassword.query.get(emp.department_id)
        if not branch_pw:
            branch_pw = BranchSupervisorPassword(department_id=emp.department_id)
            db.session.add(branch_pw)
        branch_pw.password_hash = hashed_pw
        branch_pw.password_text = new_password

    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'Password for {emp.full_name} updated successfully.'
    })

# ================= REST APIs FOR CALENDAR UI =================
@app.route('/api/baristas/search')
@login_required
def search_baristas():
    q = request.args.get('q', '').strip()
    if len(q) < 2: return jsonify([])
    
    results = db.session.query(AuthEmployee, Department.name).outerjoin(
        Department, AuthEmployee.department_id == Department.id
    ).filter(
        AuthEmployee.role.in_(['barista', 'supervisor']),
        AuthEmployee.full_name.ilike(f'%{q}%'),
        AuthEmployee.department_id != 68,
        ~Department.name.ilike('%منفک%')
    ).limit(20).all()
    
    return jsonify([{
        'id': emp.employee_number,
        'name': emp.full_name,
        'home_branch': dept_name or 'نامشخص'
    } for emp, dept_name in results])

@app.route('/api/schedule/finalize', methods=['POST'])
@login_required
@role_required('supervisor')
def api_finalize_schedule():
    data = request.json or {}
    week_start_str = data.get('week_start')

    if not data.get('confirmed'):
        return jsonify({'error': 'برای نهایی کردن باید صحت اطلاعات را تأیید کنید.'}), 400

    note = (data.get('note') or '').strip()
    if len(note) > 2000:
        return jsonify({'error': 'توضیحات نباید بیشتر از ۲۰۰۰ نویسه باشد.'}), 400
    
    try:
        week_start = datetime.strptime(week_start_str[:10], '%Y-%m-%d').date()
    except Exception:
        return jsonify({'error': 'تاریخ نامعتبر'}), 400
        
    week_end = week_start + timedelta(days=6)
    branch = current_user.branch_name
    if not branch: return jsonify({'error': 'شعبه نامشخص'}), 400
    
    b_en = branch
    b_fa = branch
    branch_info = Branch.query.filter(or_(Branch.branch_name == branch, Branch.branch_name_farsi == branch)).first()
    if branch_info:
        b_en = branch_info.branch_name
        b_fa = branch_info.branch_name_farsi
        
    existing_lock = BranchWeekLock.query.filter(
        BranchWeekLock.branch_name.in_([b_en, b_fa]),
        BranchWeekLock.week_start_date == week_start
    ).first()
    
    if not existing_lock:
        new_lock = BranchWeekLock(
            branch_name=b_fa or b_en,
            week_start_date=week_start,
            locked_by=current_user.username,
            supervisor_note=note or None
        )
        db.session.add(new_lock)
        
    shifts = OfficialShift.query.filter(
        OfficialShift.branch_name.in_([b_en, b_fa]),
        OfficialShift.start_time >= datetime.combine(week_start, datetime.min.time()),
        OfficialShift.start_time < datetime.combine(week_end + timedelta(days=1), datetime.min.time())
    ).all()
    
    for s in shifts:
        if s.assigned_employee_id:
            emp = AuthEmployee.query.filter_by(employee_number=s.assigned_employee_id).first()
            if emp and emp.department_id:
                dept = Department.query.get(emp.department_id)
                if dept and dept.name not in [b_en, b_fa]:
                    existing = GuestShiftHistory.query.filter_by(shift_id=s.id).first()
                    if not existing:
                        guest_log = GuestShiftHistory(
                            barista_employee_number=emp.employee_number,
                            barista_name=emp.full_name,
                            home_branch=dept.name,
                            guest_branch=b_fa or b_en,
                            shift_id=s.id,
                            shift_start_time=s.start_time,
                            shift_end_time=s.end_time
                        )
                        db.session.add(guest_log)
                        
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/guests')
@login_required
@role_required('admin')
def api_get_guests():
    logs = GuestShiftHistory.query.order_by(GuestShiftHistory.shift_start_time.desc()).all()
    return jsonify([{
        'id': g.id,
        'barista_name': g.barista_name,
        'home_branch': g.home_branch,
        'guest_branch': g.guest_branch,
        'shift_start': g.shift_start_time.isoformat(),
        'shift_end': g.shift_end_time.isoformat(),
        'finalized_at': g.finalized_at.isoformat()
    } for g in logs])


@app.route('/api/employees')
@login_required
@role_required('supervisor')
def api_get_employees():
    if not current_user.department_id: return jsonify([])
    emps = AuthEmployee.query.filter_by(role='barista', department_id=current_user.department_id).all()
    return jsonify([{'id': e.employee_number, 'name': e.full_name} for e in emps])

@app.route('/api/events')
@login_required
def get_events():
    branch = request.args.get('branch')
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    start_date = datetime.fromisoformat(start_str[:10]).date() if start_str else None
    end_date = datetime.fromisoformat(end_str[:10]).date() if end_str else None

    if current_user.role == 'supervisor':
        branch = current_user.branch_name
        if not branch: return jsonify([])

    events = []
    branch_info = None
    branch_english_name = branch
    branch_farsi_name = None

    if branch:
        branch_info = Branch.query.filter(or_(Branch.branch_name == branch, Branch.branch_name_farsi == branch)).first()
        if branch_info:
            branch_english_name = branch_info.branch_name
            branch_farsi_name = branch_info.branch_name_farsi

    # ================= ADMIN RECOMMENDATIONS =================
    if current_user.role == 'admin':
        if branch:
            q_rec = Schedule.query.filter(
                or_(Schedule.branch_name == branch_english_name, Schedule.branch_name == branch_farsi_name)
            ) if branch_farsi_name else Schedule.query.filter_by(branch_name=branch_english_name)
        else:
            q_rec = Schedule.query

        if start_date and end_date:
            q_rec = q_rec.filter(Schedule.target_date >= start_date, Schedule.target_date <= end_date)

        schedules = q_rec.all()
        shift_counts = {}
        stats_dict = {}

        if schedules:
            dates_set = {s.target_date for s in schedules}
            hist_dates = list({d - timedelta(days=7) for d in dates_set} | {d - timedelta(days=14) for d in dates_set})
            branches_from_schedules = list({s.branch_name for s in schedules})

            all_branches_info = Branch.query.all()
            branch_map = {}
            for b in all_branches_info:
                if b.branch_name:
                    branch_map[b.branch_name] = b.branch_name_farsi if b.branch_name_farsi else b.branch_name
                if b.branch_name_farsi:
                    branch_map[b.branch_name_farsi] = b.branch_name_farsi

            if branches_from_schedules and dates_set:
                min_d = min(dates_set)
                max_d = max(dates_set)

                all_shifts = OfficialShift.query.filter(
                    OfficialShift.branch_name.in_(branches_from_schedules),
                    OfficialShift.start_time < max_d + timedelta(days=2),
                    OfficialShift.end_time >= min_d
                ).all()

                for sh in all_shifts:
                    curr_time = sh.start_time.replace(minute=0, second=0, microsecond=0)
                    end_floor = sh.end_time.replace(minute=0, second=0, microsecond=0)
                    if sh.end_time > end_floor:
                        end_floor += timedelta(hours=1)

                    while curr_time < end_floor:
                        d = curr_time.date()
                        if d in dates_set:
                            key = (sh.branch_name, d, curr_time.hour)
                            shift_counts[key] = shift_counts.get(key, 0) + 1
                        curr_time += timedelta(hours=1)

            lookup_branch_name = branch_farsi_name if branch_farsi_name else branch_english_name
            all_stats = BranchInvoiceStats.query.filter(BranchInvoiceStats.invoicedate.in_(hist_dates))
            if branch:
                all_stats = all_stats.filter(BranchInvoiceStats.branchname == lookup_branch_name)
            else:
                search_branch_names = set(branches_from_schedules)
                for b in branches_from_schedules:
                    search_branch_names.add(branch_map.get(b, b))
                all_stats = all_stats.filter(BranchInvoiceStats.branchname.in_(list(search_branch_names)))

            for st in all_stats.all():
                stats_dict[(st.branchname, st.invoicedate, st.invoicehour)] = st

        for s in schedules:
            start_t = datetime.combine(s.target_date, datetime.min.time()).replace(hour=s.target_hour)
            shifts_count = shift_counts.get((s.branch_name, s.target_date, s.target_hour), 0)
            req_baristas = (s.required_baristas or 0)
            gap = shifts_count - req_baristas

            hist_date_1 = s.target_date - timedelta(days=7)
            hist_date_2 = s.target_date - timedelta(days=14)

            stats_branch_name = s.branch_name
            if branch_farsi_name and s.branch_name == branch_english_name:
                stats_branch_name = branch_farsi_name
            else:
                stats_branch_name = branch_map.get(s.branch_name, s.branch_name)

            inv1 = stats_dict.get((stats_branch_name, hist_date_1, int(s.target_hour)))
            inv2 = stats_dict.get((stats_branch_name, hist_date_2, int(s.target_hour)))

            inv1_invoices = inv1.totalinvoices if inv1 and inv1.totalinvoices else 0
            inv2_invoices = inv2.totalinvoices if inv2 and inv2.totalinvoices else 0
            total_invoices = round((inv1_invoices + inv2_invoices) / 2) if (inv1_invoices or inv2_invoices) else 0

            inv1_items = inv1.totalitems if inv1 and inv1.totalitems else 0
            inv2_items = inv2.totalitems if inv2 and inv2.totalitems else 0
            total_items = round((inv1_items + inv2_items) / 2, 1) if (inv1_items or inv2_items) else 0

            if gap > 0.5:
                bg_color = '#D1FAE5'
                gap_text = f'+{gap:.1f} extra'
            elif gap < -0.5:
                bg_color = '#FEE2E2'
                gap_text = f'{abs(gap):.1f} short'
            else:
                bg_color = '#F3F4F6'
                gap_text = 'exact'

            is_closed = False
            if branch_info and branch_info.opening_hour and branch_info.closing_hour:
                if start_t.time() < branch_info.opening_hour or start_t.time() >= branch_info.closing_hour:
                    is_closed = True
                    bg_color = '#E5E7EB'

            title = f"{req_baristas} Staff | {total_invoices} inv | {total_items:.0f} items"
            if shifts_count > 0: title += f" \n({gap_text})"

            events.append({
                'id': f"rec_{s.id}",
                'title': title,
                'start': start_t.isoformat(),
                'end': (start_t + timedelta(hours=1)).isoformat(),
                'display': 'background',
                'backgroundColor': bg_color,
                'classNames': ['cross-hatch'] if is_closed else [],
                'extendedProps': {
                    'type': 'recommendation',
                    'baristas': req_baristas,
                    'shifts_drawn': shifts_count,
                    'gap': gap,
                    'total_invoices': total_invoices,
                    'total_items': total_items,
                    'is_closed': is_closed
                }
            })

    # ================= SHIFT EVENTS =================
    if branch:
        if branch_farsi_name and branch_english_name:
            q_shift = OfficialShift.query.filter(
                or_(OfficialShift.branch_name == branch_english_name, OfficialShift.branch_name == branch_farsi_name)
            )
        else:
            q_shift = OfficialShift.query.filter_by(branch_name=branch)
    else:
        q_shift = OfficialShift.query

    if start_date and end_date:
        q_shift = q_shift.filter(OfficialShift.start_time < (end_date + timedelta(days=1)), OfficialShift.end_time >= start_date)

    if current_user.role == 'supervisor':
        q_shift = q_shift.filter_by(status='approved')

    shift_rows = q_shift.all()

    # Resolve category metadata in one query
    cat_map = {}
    cat_ids = {s.category_id for s in shift_rows if s.category_id}
    if cat_ids:
        for c in ShiftCategory.query.filter(ShiftCategory.id.in_(cat_ids)).all():
            cat_map[c.id] = c

    for s in shift_rows:
        is_draft = s.status == 'draft'
        assigned_text = f"\n(Assigned: {s.assigned_employee_name})" if s.assigned_employee_name else "\n(Unassigned)"
        cat = cat_map.get(s.category_id)

        base_color = '#9CA3AF' if is_draft else ('#3B82F6' if s.assigned_employee_name else '#10B981')

        events.append({
            'id': f"shift_{s.id}",
            'title': s.title + assigned_text,
            'start': s.start_time.isoformat(),
            'end': s.end_time.isoformat(),
            'color': base_color,
            # The category tints the left border so draft/assigned colours stay readable
            'borderColor': (cat.color or '#6366F1') if cat else base_color,
            'extendedProps': {
                'type': 'shift',
                'description': s.description,
                'status': s.status,
                'assigned_to': s.assigned_employee_name,
                'supervisor': s.supervisor_name,
                'category_id': s.category_id,
                'category_name': cat.name if cat else None,
                'category_color': (cat.color or '#6366F1') if cat else None
            }
        })
    return jsonify(events)

@app.route('/api/branch_info/<branch_name>', methods=['GET', 'PUT'])
@login_required
def get_or_update_branch_info(branch_name):
    branch = Branch.query.filter(or_(Branch.branch_name == branch_name, Branch.branch_name_farsi == branch_name)).first()
    if not branch: return jsonify({'error': 'Branch not found'}), 404

    if request.method == 'PUT':
        data = request.json
        def parse_time(t_str):
            if not t_str: return None
            return datetime.strptime(t_str, '%H:%M').time()
        branch.morning_shift_start = parse_time(data.get('morning_shift_start'))
        branch.morning_shift_end = parse_time(data.get('morning_shift_end'))
        branch.evening_shift_start = parse_time(data.get('evening_shift_start'))
        branch.evening_shift_end = parse_time(data.get('evening_shift_end'))
        db.session.commit()
        return jsonify({'success': True})

    return jsonify({
        'id': branch.id,
        'branch_name': branch.branch_name,
        'branch_name_farsi': branch.branch_name_farsi,
        'is_active': branch.is_active,
        'branch_area': branch.branch_area,
        'branch_code_mupra': branch.branch_code_mupra,
        'branch_status': branch.branch_status,
        'opening_hour': branch.opening_hour.strftime('%H:%M') if branch.opening_hour else None,
        'closing_hour': branch.closing_hour.strftime('%H:%M') if branch.closing_hour else None,
        'branch_minimum_staff': branch.branch_minimum_staff,
        'morning_shift_start': branch.morning_shift_start.strftime('%H:%M') if branch.morning_shift_start else None,
        'morning_shift_end': branch.morning_shift_end.strftime('%H:%M') if branch.morning_shift_end else None,
        'evening_shift_start': branch.evening_shift_start.strftime('%H:%M') if branch.evening_shift_start else None,
        'evening_shift_end': branch.evening_shift_end.strftime('%H:%M') if branch.evening_shift_end else None
    })

# ==================================================================
# SHIFT CATEGORIES
# ==================================================================

def _branch_lookup_names(branch_name):
    """Return every alias (English/Farsi) a branch may be stored under."""
    if not branch_name:
        return []
    names = {branch_name}
    info = Branch.query.filter(
        or_(Branch.branch_name == branch_name, Branch.branch_name_farsi == branch_name)
    ).first()
    if info:
        if info.branch_name:
            names.add(info.branch_name)
        if info.branch_name_farsi:
            names.add(info.branch_name_farsi)
    return list(names)


def _serialize_shift(s):
    return {
        'id': s.id,
        'branch_name': s.branch_name,
        'start_time': s.start_time.isoformat(),
        'end_time': s.end_time.isoformat(),
        'title': s.title,
        'description': s.description,
        'status': s.status,
        'assigned_employee_id': s.assigned_employee_id,
        'assigned_employee_name': s.assigned_employee_name,
        'supervisor_id': s.supervisor_id,
        'supervisor_name': s.supervisor_name,
        'category_id': s.category_id
    }


@app.route('/api/shift_categories', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def api_shift_categories():
    if request.method == 'POST':
        data = request.json or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'نام دسته‌بندی الزامی است.'}), 400

        branch = (data.get('branch') or '').strip() or None
        existing = ShiftCategory.query.filter(
            ShiftCategory.name == name,
            ShiftCategory.branch_name.is_(None) if branch is None else ShiftCategory.branch_name == branch
        ).first()
        if existing:
            return jsonify({'error': 'دسته‌بندی با این نام از قبل وجود دارد.'}), 400

        cat = ShiftCategory(
            name=name,
            color=data.get('color') or '#6366F1',
            branch_name=branch,
            description=(data.get('description') or '').strip() or None
        )
        db.session.add(cat)
        db.session.commit()
        return jsonify({'success': True, 'id': cat.id})

    # GET — categories for this branch plus the global ones
    branch = (request.args.get('branch') or '').strip()
    q = ShiftCategory.query
    if branch:
        lookup = _branch_lookup_names(branch)
        q = q.filter(or_(ShiftCategory.branch_name.is_(None), ShiftCategory.branch_name.in_(lookup)))

    categories = q.order_by(ShiftCategory.name).all()

    # Count how many shifts currently sit in each category (scoped to branch)
    counts = {}
    if categories:
        cq = db.session.query(OfficialShift.category_id, db.func.count(OfficialShift.id)) \
            .filter(OfficialShift.category_id.isnot(None))
        if branch:
            cq = cq.filter(OfficialShift.branch_name.in_(_branch_lookup_names(branch)))
        for cat_id, cnt in cq.group_by(OfficialShift.category_id).all():
            counts[cat_id] = cnt

    return jsonify([{
        'id': c.id,
        'name': c.name,
        'color': c.color or '#6366F1',
        'branch_name': c.branch_name,
        'description': c.description,
        'is_global': c.branch_name is None,
        'shift_count': counts.get(c.id, 0)
    } for c in categories])


@app.route('/api/shift_categories/<int:id>', methods=['PUT', 'DELETE'])
@login_required
@role_required('admin')
def api_modify_shift_category(id):
    cat = ShiftCategory.query.get_or_404(id)

    if request.method == 'DELETE':
        # Detach shifts rather than deleting them
        OfficialShift.query.filter_by(category_id=cat.id).update(
            {'category_id': None}, synchronize_session=False
        )
        db.session.delete(cat)
        db.session.commit()
        return jsonify({'success': True})

    data = request.json or {}
    if 'name' in data:
        new_name = (data.get('name') or '').strip()
        if not new_name:
            return jsonify({'error': 'نام دسته‌بندی نمی‌تواند خالی باشد.'}), 400
        cat.name = new_name
    if 'color' in data:
        cat.color = data.get('color') or '#6366F1'
    if 'description' in data:
        cat.description = (data.get('description') or '').strip() or None

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/shift_categories/<int:id>/adjust', methods=['POST'])
@login_required
@role_required('admin')
def api_adjust_shift_category(id):
    """Bulk time-shift every shift in a category.

    mode:    'move'  -> slide start AND end together
             'start' -> move only the start (changes duration)
             'end'   -> move only the end   (changes duration)
    minutes: signed. positive = later / forward, negative = earlier / backward.
    scope:   'week' (needs week_start) | 'range' (start_date & end_date)
             | 'future' (today onward) | 'all'
    """
    cat = ShiftCategory.query.get_or_404(id)
    data = request.json or {}

    mode = (data.get('mode') or 'move').strip()
    if mode not in ('move', 'start', 'end'):
        return jsonify({'error': 'حالت نامعتبر است.'}), 400

    try:
        minutes = int(data.get('minutes'))
    except (TypeError, ValueError):
        return jsonify({'error': 'مقدار دقیقه نامعتبر است.'}), 400

    if minutes == 0:
        return jsonify({'error': 'مقدار جابجایی نمی‌تواند صفر باشد.'}), 400

    delta = timedelta(minutes=minutes)
    scope = (data.get('scope') or 'week').strip()
    branch = (data.get('branch') or '').strip()

    q = OfficialShift.query.filter(OfficialShift.category_id == cat.id)

    if branch:
        q = q.filter(OfficialShift.branch_name.in_(_branch_lookup_names(branch)))

    try:
        if scope == 'week':
            week_start_str = data.get('week_start')
            if not week_start_str:
                return jsonify({'error': 'تاریخ شروع هفته ارسال نشده است.'}), 400
            week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
            lo = datetime.combine(week_start, datetime.min.time())
            hi = lo + timedelta(days=7)
            q = q.filter(OfficialShift.start_time >= lo, OfficialShift.start_time < hi)

        elif scope == 'range':
            start_str = data.get('start_date')
            end_str = data.get('end_date')
            if not start_str or not end_str:
                return jsonify({'error': 'بازه تاریخ کامل نیست.'}), 400
            lo = datetime.combine(datetime.strptime(start_str, '%Y-%m-%d').date(), datetime.min.time())
            hi = datetime.combine(datetime.strptime(end_str, '%Y-%m-%d').date(), datetime.min.time()) + timedelta(days=1)
            q = q.filter(OfficialShift.start_time >= lo, OfficialShift.start_time < hi)

        elif scope == 'future':
            lo = datetime.combine(datetime.now().date(), datetime.min.time())
            q = q.filter(OfficialShift.start_time >= lo)

        elif scope != 'all':
            return jsonify({'error': 'محدوده نامعتبر است.'}), 400

    except ValueError:
        return jsonify({'error': 'فرمت تاریخ نامعتبر است.'}), 400

    shifts = q.all()
    if not shifts:
        return jsonify({'success': True, 'count': 0,
                        'message': 'هیچ شیفتی در این دسته‌بندی و محدوده پیدا نشد.'})

    # Validate the whole batch BEFORE writing anything
    invalid = []
    for s in shifts:
        new_start = s.start_time + delta if mode in ('move', 'start') else s.start_time
        new_end = s.end_time + delta if mode in ('move', 'end') else s.end_time
        if new_end <= new_start:
            invalid.append(f"#{s.id} ({s.start_time.strftime('%Y-%m-%d %H:%M')})")

    if invalid:
        return jsonify({
            'error': 'این تغییر باعث می‌شود زمان پایان قبل از شروع قرار گیرد: '
                     + '، '.join(invalid[:5])
                     + ('...' if len(invalid) > 5 else '')
        }), 400

    serialized_old = [_serialize_shift(s) for s in shifts]

    for s in shifts:
        if mode in ('move', 'start'):
            s.start_time = s.start_time + delta
        if mode in ('move', 'end'):
            s.end_time = s.end_time + delta

    log_action('update', {'shifts': serialized_old})
    db.session.commit()

    direction = 'جلوتر' if minutes > 0 else 'عقب‌تر'
    label = {'move': 'کل شیفت', 'start': 'زمان شروع', 'end': 'زمان پایان'}[mode]
    return jsonify({
        'success': True,
        'count': len(shifts),
        'message': f"{label} برای {len(shifts)} شیفت دسته «{cat.name}» "
                   f"{abs(minutes)} دقیقه {direction} شد."
    })


# ==================================================================
# SHIFT TEMPLATES (weekly grid)
# ==================================================================

# Iranian week order. Index = offset from the Saturday that starts the week.
DAY_KEYS = ['sat', 'sun', 'mon', 'tue', 'wed', 'thu', 'fri']
DAY_LABELS = {
    'sat': 'شنبه', 'sun': 'یکشنبه', 'mon': 'دوشنبه', 'tue': 'سه‌شنبه',
    'wed': 'چهارشنبه', 'thu': 'پنج‌شنبه', 'fri': 'جمعه'
}
# Saturday drives these. Thursday and Friday are always independent.
INHERIT_KEYS = ['sun', 'mon', 'tue', 'wed']


def _default_day_config():
    cfg = {k: {'mode': 'default'} for k in DAY_KEYS[:5]}   # sat..wed on
    cfg['thu'] = {'mode': 'off'}
    cfg['fri'] = {'mode': 'off'}
    return cfg


def _parse_hhmm(value, field):
    """'09:30' -> time(9, 30). Raises ValueError with a Farsi message."""
    try:
        parts = str(value).strip().split(':')
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return dt_time(h, m)
    except Exception:
        raise ValueError(f'ساعت «{field}» نامعتبر است (قالب درست: HH:MM).')


def _serialize_template(t):
    return {
        'id': t.id,
        'branch_name': t.branch_name,
        'title': t.title,
        'start': t.start_time.strftime('%H:%M'),
        'end': t.end_time.strftime('%H:%M'),
        'category_id': t.category_id,
        'day_config': t.day_config or _default_day_config(),
        'sort_order': t.sort_order or 0
    }


def _resolve_day(template, day_key):
    """Return (start_time, end_time) for a day, or None when disabled."""
    cfg = (template.day_config or {}).get(day_key) or {'mode': 'default'}
    mode = cfg.get('mode', 'default')

    if mode == 'off':
        return None
    if mode == 'custom':
        try:
            return _parse_hhmm(cfg.get('start'), day_key), _parse_hhmm(cfg.get('end'), day_key)
        except ValueError:
            return None
    return template.start_time, template.end_time


@app.route('/api/shift_templates', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def api_shift_templates():
    if request.method == 'POST':
        data = request.json or {}
        branch = (data.get('branch') or '').strip()
        title = (data.get('title') or '').strip()

        if not branch:
            return jsonify({'error': 'ابتدا یک شعبه انتخاب کنید.'}), 400
        if not title:
            return jsonify({'error': 'عنوان شیفت الزامی است.'}), 400

        try:
            start_t = _parse_hhmm(data.get('start', '08:00'), 'شروع')
            end_t = _parse_hhmm(data.get('end', '16:00'), 'پایان')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        last = db.session.query(db.func.max(ShiftTemplate.sort_order)) \
            .filter(ShiftTemplate.branch_name == branch).scalar()

        tpl = ShiftTemplate(
            branch_name=branch,
            title=title,
            start_time=start_t,
            end_time=end_t,
            category_id=data.get('category_id') or None,
            day_config=data.get('day_config') or _default_day_config(),
            sort_order=(last or 0) + 1
        )
        db.session.add(tpl)
        db.session.commit()
        return jsonify({'success': True, 'template': _serialize_template(tpl)})

    branch = (request.args.get('branch') or '').strip()
    if not branch:
        return jsonify([])

    rows = ShiftTemplate.query.filter(
        ShiftTemplate.branch_name.in_(_branch_lookup_names(branch))
    ).order_by(ShiftTemplate.sort_order, ShiftTemplate.id).all()

    return jsonify([_serialize_template(t) for t in rows])


@app.route('/api/shift_templates/<int:id>', methods=['PUT', 'DELETE'])
@login_required
@role_required('admin')
def api_modify_shift_template(id):
    tpl = ShiftTemplate.query.get_or_404(id)

    if request.method == 'DELETE':
        db.session.delete(tpl)
        db.session.commit()
        return jsonify({'success': True})

    data = request.json or {}
    try:
        if 'title' in data:
            title = (data.get('title') or '').strip()
            if not title:
                return jsonify({'error': 'عنوان شیفت نمی‌تواند خالی باشد.'}), 400
            tpl.title = title
        if 'start' in data:
            tpl.start_time = _parse_hhmm(data['start'], 'شروع')
        if 'end' in data:
            tpl.end_time = _parse_hhmm(data['end'], 'پایان')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    if 'category_id' in data:
        tpl.category_id = data.get('category_id') or None
    if 'day_config' in data:
        # flag_modified is unnecessary because we assign a fresh dict
        tpl.day_config = data.get('day_config') or _default_day_config()

    db.session.commit()
    return jsonify({'success': True, 'template': _serialize_template(tpl)})


@app.route('/api/shift_templates/generate', methods=['POST'])
@login_required
@role_required('admin')
def api_generate_from_templates():
    """Materialise the template grid into real shifts for a given week."""
    data = request.json or {}
    branch = (data.get('branch') or '').strip()
    week_start_str = data.get('week_start')
    overwrite = bool(data.get('overwrite'))

    if not branch or not week_start_str:
        return jsonify({'error': 'شعبه یا تاریخ شروع هفته مشخص نیست.'}), 400

    try:
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'فرمت تاریخ نامعتبر است.'}), 400

    templates = ShiftTemplate.query.filter(
        ShiftTemplate.branch_name.in_(_branch_lookup_names(branch))
    ).order_by(ShiftTemplate.sort_order, ShiftTemplate.id).all()

    if not templates:
        return jsonify({'error': 'هیچ الگوی شیفتی برای این شعبه تعریف نشده است.'}), 404

    lo = datetime.combine(week_start, datetime.min.time())
    hi = lo + timedelta(days=7)
    lookup = _branch_lookup_names(branch)

    existing_q = OfficialShift.query.filter(
        OfficialShift.branch_name.in_(lookup),
        OfficialShift.start_time >= lo,
        OfficialShift.start_time < hi
    )

    removed = 0
    if overwrite:
        victims = existing_q.all()
        serialized_old = [_serialize_shift(s) for s in victims]
        for s in victims:
            db.session.add(DeletedOfficialShift(
                original_id=s.id, branch_name=s.branch_name,
                start_time=s.start_time, end_time=s.end_time,
                title=s.title, description=s.description, status=s.status,
                assigned_employee_id=s.assigned_employee_id,
                assigned_employee_name=s.assigned_employee_name,
                supervisor_id=s.supervisor_id, supervisor_name=s.supervisor_name,
                category_id=s.category_id
            ))
            db.session.delete(s)
        removed = len(victims)
        if serialized_old:
            log_action('delete', {'shifts': serialized_old})
        db.session.flush()
    elif existing_q.count() > 0:
        return jsonify({
            'error': 'این هفته از قبل شیفت دارد. برای جایگزینی، گزینهٔ '
                     '«جایگزینی شیفت‌های موجود» را فعال کنید.'
        }), 409

    created = []
    skipped = []
    for tpl in templates:
        for offset, day_key in enumerate(DAY_KEYS):
            resolved = _resolve_day(tpl, day_key)
            if resolved is None:
                continue

            start_t, end_t = resolved
            day_date = week_start + timedelta(days=offset)
            start_dt = datetime.combine(day_date, start_t)
            end_dt = datetime.combine(day_date, end_t)

            # An end earlier than the start means the shift runs past midnight.
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            if (end_dt - start_dt) > timedelta(hours=24):
                skipped.append(f"{tpl.title} / {DAY_LABELS[day_key]}")
                continue

            created.append(OfficialShift(
                branch_name=branch,
                title=tpl.title,
                description='ساخته‌شده از الگوی هفتگی',
                start_time=start_dt,
                end_time=end_dt,
                status='draft',
                category_id=tpl.category_id
            ))

    if not created:
        db.session.rollback()
        return jsonify({'error': 'هیچ شیفتی تولید نشد — همهٔ روزها غیرفعال هستند.'}), 400

    db.session.add_all(created)
    db.session.flush()
    log_action('insert', {'shift_ids': [s.id for s in created]})
    db.session.commit()

    msg = f'{len(created)} شیفت برای هفتهٔ {week_start_str} ساخته شد.'
    if removed:
        msg += f' ({removed} شیفت قبلی جایگزین شد.)'
    if skipped:
        msg += f' {len(skipped)} مورد به دلیل بازهٔ نامعتبر رد شد.'

    return jsonify({'success': True, 'count': len(created),
                    'removed': removed, 'skipped': skipped, 'message': msg})


@app.route('/api/shifts', methods=['POST'])
@login_required
@role_required('admin')
def api_create_shift():
    data = request.json
    start_dt = datetime.strptime(data['start'], '%Y-%m-%dT%H:%M:%S')
    end_dt = datetime.strptime(data['end'], '%Y-%m-%dT%H:%M:%S')
    new_shift = OfficialShift(branch_name=data['branch'], title=data['title'], description=data['description'],
                              start_time=start_dt, end_time=end_dt, status='draft',
                              category_id=data.get('category_id') or None)
    db.session.add(new_shift)
    db.session.flush()

    # Log Undo Creation
    log_action('insert', {'shift_ids': [new_shift.id]})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/shifts/<int:id>', methods=['PUT', 'DELETE'])
@login_required
@role_required('admin')
def api_modify_shift(id):
    shift = OfficialShift.query.get_or_404(id)

    if request.method == 'DELETE':
        serialized_old = _serialize_shift(shift)
        archived_shift = DeletedOfficialShift(
            original_id=shift.id, branch_name=shift.branch_name,
            start_time=shift.start_time, end_time=shift.end_time,
            title=shift.title, description=shift.description, status=shift.status,
            assigned_employee_id=shift.assigned_employee_id, assigned_employee_name=shift.assigned_employee_name,
            supervisor_id=shift.supervisor_id, supervisor_name=shift.supervisor_name,
            category_id=shift.category_id
        )
        db.session.add(archived_shift)
        db.session.delete(shift)

        # Log Undo Deletion
        log_action('delete', {'shifts': [serialized_old]})
        db.session.commit()
        return jsonify({'success': True})

    # PUT update
    data = request.json
    serialized_old = _serialize_shift(shift)
    shift.title = data['title']
    shift.description = data['description']
    shift.start_time = datetime.strptime(data['start'], '%Y-%m-%dT%H:%M:%S')
    shift.end_time = datetime.strptime(data['end'], '%Y-%m-%dT%H:%M:%S')
    if 'category_id' in data:
        shift.category_id = data.get('category_id') or None

    # Log Undo Update
    log_action('update', {'shifts': [serialized_old]})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/shifts/approve_all', methods=['POST'])
@login_required
@role_required('admin')
def api_approve_all():
    branch_req = request.json.get('branch')
    q = OfficialShift.query.filter_by(status='draft')
    if branch_req:
        branch_info = Branch.query.filter(or_(Branch.branch_name == branch_req, Branch.branch_name_farsi == branch_req)).first()
        if branch_info and branch_info.branch_name_farsi:
            q = q.filter(or_(OfficialShift.branch_name == branch_info.branch_name, OfficialShift.branch_name == branch_info.branch_name_farsi))
        else:
            q = q.filter_by(branch_name=branch_req)
    shifts = q.all()
    for s in shifts:
        s.status = 'approved'
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/shifts/auto_generate', methods=['POST'])
@login_required
@role_required('admin')
def api_auto_generate_shifts():
    data = request.json
    branch_name = data.get('branch')
    start_date_str = data.get('start_date')
    end_date_str = data.get('end_date')

    if not branch_name or not start_date_str or not end_date_str:
        return jsonify({'error': 'Missing parameters'}), 400

    branch = Branch.query.filter(or_(Branch.branch_name == branch_name, Branch.branch_name_farsi == branch_name)).first()
    if not branch or not branch.morning_shift_start or not branch.evening_shift_end:
        return jsonify({'error': 'Please configure Morning and Evening shift times for this branch first.'}), 400

    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d').date()

    lookup_names = [branch.branch_name]
    if branch.branch_name_farsi: lookup_names.append(branch.branch_name_farsi)

    schedules = Schedule.query.filter(
        Schedule.branch_name.in_(lookup_names),
        Schedule.target_date >= start_dt,
        Schedule.target_date <= end_dt
    ).all()

    from collections import defaultdict
    daily_reqs = defaultdict(lambda: {'morning': 0, 'evening': 0})

    m_start = branch.morning_shift_start.hour
    m_end = branch.morning_shift_end.hour
    e_start = branch.evening_shift_start.hour
    e_end = branch.evening_shift_end.hour
    if e_end == 0: e_end = 24

    for s in schedules:
        hr = s.target_hour
        req = s.required_baristas or 0
        if req > 0:
            if m_start <= hr < m_end:
                if req > daily_reqs[s.target_date]['morning']:
                    daily_reqs[s.target_date]['morning'] = req
            if e_start <= hr < e_end:
                if req > daily_reqs[s.target_date]['evening']:
                    daily_reqs[s.target_date]['evening'] = req

    new_shifts = []
    for date, reqs in daily_reqs.items():
        m_count = math.ceil(reqs['morning'])
        e_count = math.ceil(reqs['evening'])

        for _ in range(m_count):
            st = datetime.combine(date, branch.morning_shift_start)
            et = datetime.combine(date, branch.morning_shift_end)
            new_shifts.append(OfficialShift(
                branch_name=branch.branch_name,
                title="Morning Shift",
                description="Auto-generated based on peak traffic",
                start_time=st, end_time=et, status='draft'
            ))

        for _ in range(e_count):
            st = datetime.combine(date, branch.evening_shift_start)
            if branch.evening_shift_end.hour == 0 and branch.evening_shift_end.minute == 0:
                et = datetime.combine(date + timedelta(days=1), branch.evening_shift_end)
            else:
                et = datetime.combine(date, branch.evening_shift_end)

            new_shifts.append(OfficialShift(
                branch_name=branch.branch_name,
                title="Evening Shift",
                description="Auto-generated based on peak traffic",
                start_time=st, end_time=et, status='draft'
            ))

    if new_shifts:
        db.session.add_all(new_shifts)
        db.session.flush()
        log_action('insert', {'shift_ids': [item.id for item in new_shifts]})
        db.session.commit()

    return jsonify({'success': True, 'message': f'Automatically drew {len(new_shifts)} Official Shifts.'})

@app.route('/api/shifts/copy_week', methods=['POST'])
@login_required
@role_required('admin')
def api_copy_week_shifts():
    data = request.json
    branch_name = data.get('branch')
    from_str    = data.get('from_date')
    to_str      = data.get('to_date')

    if not branch_name or not from_str or not to_str:
        return jsonify({'error': 'Missing branch, from_date, or to_date'}), 400

    from_monday = datetime.strptime(from_str, '%Y-%m-%d').date()
    to_monday   = datetime.strptime(to_str,   '%Y-%m-%d').date()

    branch = Branch.query.filter(or_(Branch.branch_name == branch_name, Branch.branch_name_farsi == branch_name)).first()
    lookup_names = [branch_name]
    if branch:
        if branch.branch_name:       lookup_names.append(branch.branch_name)
        if branch.branch_name_farsi: lookup_names.append(branch.branch_name_farsi)
    lookup_names = list(set(lookup_names))

    from_start = datetime.combine(from_monday, datetime.min.time())
    from_end   = datetime.combine(from_monday + timedelta(days=7), datetime.min.time())

    source_shifts = OfficialShift.query.filter(
        OfficialShift.branch_name.in_(lookup_names),
        OfficialShift.start_time >= from_start,
        OfficialShift.start_time <  from_end
    ).all()

    if not source_shifts:
        return jsonify({'success': False, 'message': 'No shifts found in the source week for this branch.'}), 404

    new_items = []
    for s in source_shifts:
        day_offset = (s.start_time.date() - from_monday).days
        new_start  = datetime.combine(to_monday + timedelta(days=day_offset), s.start_time.time())
        new_end    = datetime.combine(to_monday + timedelta(days=day_offset), s.end_time.time())
        if new_end <= new_start:
            new_end += timedelta(days=1)

        new_items.append(OfficialShift(
            branch_name=s.branch_name,
            title=s.title,
            description=s.description,
            start_time=new_start,
            end_time=new_end,
            status='draft'
        ))

    db.session.add_all(new_items)
    db.session.flush()
    log_action('insert', {'shift_ids': [item.id for item in new_items]})
    db.session.commit()
    return jsonify({'success': True, 'message': f'Copied {len(new_items)} shifts to week of {to_monday}.'})

@app.route('/api/shifts/<int:id>/assign', methods=['POST'])
@login_required
@role_required('supervisor')
def api_assign_shift(id):
    shift = OfficialShift.query.get_or_404(id)
    shift_date = shift.start_time.date()

    # 1. Deadline Check
    dl = ShiftDeadline.query.filter(
        ShiftDeadline.period_start <= shift_date,
        ShiftDeadline.period_end >= shift_date
    ).order_by(ShiftDeadline.id.desc()).first()

    if dl and datetime.now() > dl.deadline_datetime:
        return jsonify({
            'error': f'Deadline passed! Shifts for this date ({shift_date}) were locked on {dl.deadline_datetime.strftime("%Y-%m-%d %H:%M")}.'
        }), 403

    # 2. Branch Access Check
    user_branch_en = current_user.branch_name
    user_branch_fa = current_user.branch_name
    if current_user.branch_name:
        branch_info = Branch.query.filter(or_(Branch.branch_name == current_user.branch_name, Branch.branch_name_farsi == current_user.branch_name)).first()
        if branch_info:
            user_branch_en = branch_info.branch_name
            user_branch_fa = branch_info.branch_name_farsi

    if shift.branch_name not in [user_branch_en, user_branch_fa]:
        return jsonify({'error': 'Permission denied'}), 403

    # 3. Week Lock Check
    days_back = (shift_date.weekday() - 5) % 7
    week_start = shift_date - timedelta(days=days_back)
    week_end = week_start + timedelta(days=6)
    
    lock = BranchWeekLock.query.filter(
        BranchWeekLock.branch_name.in_([user_branch_en, user_branch_fa]),
        BranchWeekLock.week_start_date == week_start
    ).first()
    
    if lock:
        return jsonify({'error': 'برنامه این هفته نهایی شده و قابل تغییر نیست.'}), 403

    data = request.json
    emp_id = data.get('emp_id')
    emp_name = data.get('emp_name')

    # Allow unassigning
    if not emp_id:
        shift.assigned_employee_id = None
        shift.assigned_employee_name = None
        shift.supervisor_id = current_user.username
        shift.supervisor_name = current_user.full_name
        db.session.commit()
        return jsonify({'success': True})

    # ==================== SUPERVISOR SCHEDULING RULES ====================

    # RULE 2: Max 1 Shift Per Day
    same_day_shift = OfficialShift.query.filter(
        OfficialShift.id != shift.id,
        OfficialShift.assigned_employee_id == emp_id,
        db.func.date(OfficialShift.start_time) == shift_date
    ).first()

    if same_day_shift:
        return jsonify({'error': f'{emp_name} در تاریخ {shift_date} قبلاً شیفت دارد (حداکثر ۱ شیفت در روز).'}), 400

    # RULE 1: Max 6 Work Days Per Week
    week_shifts = OfficialShift.query.filter(
        OfficialShift.id != shift.id,
        OfficialShift.assigned_employee_id == emp_id,
        OfficialShift.start_time >= datetime.combine(week_start, datetime.min.time()),
        OfficialShift.start_time < datetime.combine(week_end + timedelta(days=1), datetime.min.time())
    ).all()

    work_dates = {s.start_time.date() for s in week_shifts}
    work_dates.add(shift_date)

    if len(work_dates) > 6:
        return jsonify({'error': f'{emp_name} نمی‌تواند ۷ روز در هفته کار کند (حداکثر ۶ روز در هفته مجاز است).'}), 400

    # RULE 3: Minimum 8 Hours and 30 Minutes Rest Between Consecutive Shifts
    MIN_REST = timedelta(hours=8, minutes=30)
    
    nearby_start = datetime.combine(shift_date - timedelta(days=1), datetime.min.time())
    nearby_end = datetime.combine(shift_date + timedelta(days=2), datetime.min.time())

    adjacent_shifts = OfficialShift.query.filter(
        OfficialShift.id != shift.id,
        OfficialShift.assigned_employee_id == emp_id,
        OfficialShift.start_time >= nearby_start,
        OfficialShift.start_time < nearby_end
    ).all()

    for s in adjacent_shifts:
        # Shift ending BEFORE new shift starts
        if s.end_time <= shift.start_time:
            gap = shift.start_time - s.end_time
            if gap < MIN_REST:
                hrs = int(gap.total_seconds() // 3600)
                mins = int((gap.total_seconds() % 3600) // 60)
                return jsonify({
                    'error': f'استراحت بین پایان شیفت قبل ({s.end_time.strftime("%H:%M")}) و شروع شیفت جدید ({shift.start_time.strftime("%H:%M")}) کمتر از ۸ ساعت و ۳۰ دقیقه است. (فاصله فعلی: {hrs} ساعت و {mins} دقیقه)'
                }), 400

        # Shift starting AFTER new shift ends
        if shift.end_time <= s.start_time:
            gap = s.start_time - shift.end_time
            if gap < MIN_REST:
                hrs = int(gap.total_seconds() // 3600)
                mins = int((gap.total_seconds() % 3600) // 60)
                return jsonify({
                    'error': f'استراحت بین پایان شیفت جدید ({shift.end_time.strftime("%H:%M")}) و شروع شیفت بعدی ({s.start_time.strftime("%H:%M")}) کمتر از ۸ ساعت و ۳۰ دقیقه است. (فاصله فعلی: {hrs} ساعت و {mins} دقیقه)'
                }), 400

    # =====================================================================

    # All checks passed, assign shift
    shift.assigned_employee_id = emp_id
    shift.assigned_employee_name = emp_name
    shift.supervisor_id = current_user.username
    shift.supervisor_name = current_user.full_name
    
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/branches/<int:id>/update', methods=['PUT'])
@login_required
@role_required('admin', 'branch_editor')
def api_update_branch(id):
    branch = Branch.query.get_or_404(id)
    data = request.json

    def parse_time(t_str):
        if not t_str: return None
        try:
            return datetime.strptime(t_str, '%H:%M').time()
        except (ValueError, TypeError):
             return None

    try:
        branch.opening_hour = parse_time(data.get('opening_hour'))
        branch.closing_hour = parse_time(data.get('closing_hour'))
        branch.morning_shift_start = parse_time(data.get('morning_shift_start'))
        branch.morning_shift_end = parse_time(data.get('morning_shift_end'))
        branch.evening_shift_start = parse_time(data.get('evening_shift_start'))
        branch.evening_shift_end = parse_time(data.get('evening_shift_end'))
        
        # NEW: Handle note updating
        if 'notes' in data:
            branch.notes = data.get('notes')

        min_staff = data.get('branch_minimum_staff')
        branch.branch_minimum_staff = int(min_staff) if min_staff and str(min_staff).isdigit() else None

        db.session.commit()
        return jsonify({'success': True, 'message': 'Branch updated successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/api/branches/<int:id>/toggle_seal', methods=['POST'])
@login_required
@role_required('admin', 'branch_editor')
def api_toggle_seal(id):
    branch = Branch.query.get_or_404(id)
    try:
        new_status = ''
        if branch.branch_status == 'active':
            branch.branch_status = 'sealed'
            new_status = 'sealed'
            seal_record = SealHistory(branch_name_farsi=branch.branch_name_farsi, datetime=datetime.utcnow())
            db.session.add(seal_record)
        elif branch.branch_status == 'sealed':
            branch.branch_status = 'active'
            new_status = 'active'
        else:
            return jsonify({'success': False, 'message': f'Cannot seal/unseal a branch with status {branch.branch_status}'}), 400

        db.session.commit()
        return jsonify({'success': True, 'new_status': new_status, 'message': f'Branch status changed to {new_status}.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/deadlines', methods=['GET', 'POST'])
@login_required
def api_deadlines():
    if request.method == 'POST':
        if current_user.role != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403
        data = request.json
        p_start = datetime.strptime(data.get('period_start'), '%Y-%m-%d').date()
        p_end = datetime.strptime(data.get('period_end'), '%Y-%m-%d').date()
        d_time = datetime.strptime(data.get('deadline'), '%Y-%m-%dT%H:%M')
        new_dl = ShiftDeadline(period_start=p_start, period_end=p_end, deadline_datetime=d_time)
        db.session.add(new_dl)
        db.session.commit()
        return jsonify({'success': True})

    start_str = request.args.get('start')
    end_str = request.args.get('end')
    if not start_str or not end_str:
        return jsonify([])

    start_d = datetime.fromisoformat(start_str[:10]).date()
    end_d = datetime.fromisoformat(end_str[:10]).date()
    deadlines = ShiftDeadline.query.filter(
        ShiftDeadline.period_start <= end_d,
        ShiftDeadline.period_end >= start_d
    ).order_by(ShiftDeadline.id.desc()).all()

    return jsonify([{
        'id': d.id,
        'period_start': d.period_start.isoformat(),
        'period_end': d.period_end.isoformat(),
        'deadline': d.deadline_datetime.isoformat()
    } for d in deadlines])

@app.route('/api/deadline', methods=['GET', 'POST'])
@login_required
def api_deadline():
    if request.method == 'POST':
        if current_user.role != 'admin':
            return jsonify({'error': 'Unauthorized'}), 403
        data = request.json
        week_start_str = data.get('week_start')
        deadline_str = data.get('deadline')
        if not week_start_str or not deadline_str:
            return jsonify({'error': 'Missing parameters'}), 400
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
        deadline_dt = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        wd = WeeklyDeadline.query.filter_by(week_start_date=week_start).first()
        if not wd:
            wd = WeeklyDeadline(week_start_date=week_start)
            db.session.add(wd)
        wd.deadline_datetime = deadline_dt
        db.session.commit()
        return jsonify({'success': True})

    week_start_str = request.args.get('week_start')
    if not week_start_str:
        return jsonify({})
    week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
    wd = WeeklyDeadline.query.filter_by(week_start_date=week_start).first()
    if wd:
        return jsonify({'deadline': wd.deadline_datetime.strftime('%Y-%m-%dT%H:%M')})
    return jsonify({})

@app.route('/api/shifts/bulk', methods=['POST'])
@login_required
@role_required('admin')
def api_bulk_create_shifts():
    data = request.json
    shifts = data.get('shifts', [])
    new_items = []
    for s in shifts:
        st = datetime.strptime(s['start'], '%Y-%m-%dT%H:%M:%S')
        et = datetime.strptime(s['end'], '%Y-%m-%dT%H:%M:%S')
        new_items.append(OfficialShift(
            branch_name=s['branch'],
            title=s['title'],
            description=s.get('description', ''),
            start_time=st,
            end_time=et,
            status='draft',
            category_id=s.get('category_id') or None
        ))
    if new_items:
        db.session.add_all(new_items)
        db.session.flush()
        log_action('insert', {'shift_ids': [item.id for item in new_items]})
        db.session.commit()
    return jsonify({'success': True, 'count': len(new_items)})

# ================= NEW UNDO ENDPOINT & COMPANIONS =================

@app.route('/api/undo', methods=['POST'])
@login_required
@role_required('admin')
def api_undo():
    last_log = ActionLog.query.order_by(ActionLog.timestamp.desc()).first()
    if not last_log:
        return jsonify({'success': False, 'message': 'No recent actions to undo.'}), 400
    try:
        action_type = last_log.action_type
        details = last_log.details

        if action_type == 'insert':
            shift_ids = details.get('shift_ids', [])
            if shift_ids:
                shifts_to_delete = OfficialShift.query.filter(OfficialShift.id.in_(shift_ids)).all()
                for s in shifts_to_delete:
                    archived = DeletedOfficialShift(
                        original_id=s.id, branch_name=s.branch_name,
                        start_time=s.start_time, end_time=s.end_time,
                        title=s.title, description=s.description, status=s.status,
                        assigned_employee_id=s.assigned_employee_id, assigned_employee_name=s.assigned_employee_name,
                        supervisor_id=s.supervisor_id, supervisor_name=s.supervisor_name
                    )
                    db.session.add(archived)
                    db.session.delete(s)
            message = f"Undid creation of {len(shift_ids)} shift(s)."

        elif action_type == 'delete':
            shifts_data = details.get('shifts', [])
            restored_count = 0
            for s_data in shifts_data:
                restored_shift = OfficialShift(
                    branch_name=s_data['branch_name'],
                    start_time=datetime.fromisoformat(s_data['start_time']),
                    end_time=datetime.fromisoformat(s_data['end_time']),
                    title=s_data['title'],
                    description=s_data['description'],
                    status=s_data['status'],
                    assigned_employee_id=s_data.get('assigned_employee_id'),
                    assigned_employee_name=s_data.get('assigned_employee_name'),
                    supervisor_id=s_data.get('supervisor_id'),
                    supervisor_name=s_data.get('supervisor_name'),
                    category_id=s_data.get('category_id')
                )
                db.session.add(restored_shift)
                restored_count += 1
            message = f"Restored {restored_count} deleted shift(s)."

        elif action_type == 'update':
            shifts_data = details.get('shifts', [])
            updated_count = 0
            for s_data in shifts_data:
                shift = OfficialShift.query.get(s_data['id'])
                if shift:
                    shift.title = s_data['title']
                    shift.description = s_data['description']
                    shift.start_time = datetime.fromisoformat(s_data['start_time'])
                    shift.end_time = datetime.fromisoformat(s_data['end_time'])
                    shift.status = s_data['status']
                    shift.assigned_employee_id = s_data.get('assigned_employee_id')
                    shift.assigned_employee_name = s_data.get('assigned_employee_name')
                    shift.supervisor_id = s_data.get('supervisor_id')
                    shift.supervisor_name = s_data.get('supervisor_name')
                    if 'category_id' in s_data:
                        shift.category_id = s_data.get('category_id')
                    updated_count += 1
            message = f"Reverted changes for {updated_count} updated shift(s)."
        else:
            return jsonify({'success': False, 'message': 'Unknown action type.'}), 400

        db.session.delete(last_log)
        db.session.commit()
        return jsonify({'success': True, 'message': message})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f"Error during undo: {str(e)}"}), 500

@app.route('/api/shifts/bulk_delete', methods=['POST'])
@login_required
@role_required('admin')
def api_bulk_delete_shifts():
    data = request.json
    shift_ids = data.get('ids', [])
    if not shift_ids:
        return jsonify({'success': False, 'message': 'No IDs provided.'}), 400

    shifts = OfficialShift.query.filter(OfficialShift.id.in_(shift_ids)).all()
    if not shifts:
        return jsonify({'success': True, 'count': 0})

    serialized_old = []
    for s in shifts:
        serialized_old.append(_serialize_shift(s))
        archived = DeletedOfficialShift(
            original_id=s.id, branch_name=s.branch_name,
            start_time=s.start_time, end_time=s.end_time,
            title=s.title, description=s.description, status=s.status,
            assigned_employee_id=s.assigned_employee_id, assigned_employee_name=s.assigned_employee_name,
            supervisor_id=s.supervisor_id, supervisor_name=s.supervisor_name,
            category_id=s.category_id
        )
        db.session.add(archived)
        db.session.delete(s)

    log_action('delete', {'shifts': serialized_old})
    db.session.commit()
    return jsonify({'success': True, 'count': len(shifts)})

@app.route('/api/shifts/global_copy_last', methods=['POST'])
@login_required
@role_required('admin')
def api_global_copy_last():
    data = request.json
    to_date_str = data.get('to_date')
    if not to_date_str:
        return jsonify({"error": "Missing target date"}), 400

    to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
    branches = db.session.query(Branch.branch_name).filter(Branch.branch_status != 'discontinued').distinct().all()
    branch_names = [b[0] for b in branches if b[0]]
    new_items = []

    for b_name in branch_names:
        found_shifts = []
        source_week_start = None
        for offset_weeks in range(1, 13):
            test_week_start = to_date - timedelta(weeks=offset_weeks)
            test_week_end = test_week_start + timedelta(days=7)
            test_start_dt = datetime.combine(test_week_start, datetime.min.time())
            test_end_dt = datetime.combine(test_week_end, datetime.min.time())
            shifts = OfficialShift.query.filter(
                OfficialShift.branch_name == b_name,
                OfficialShift.status == 'approved',
                OfficialShift.start_time >= test_start_dt,
                OfficialShift.start_time < test_end_dt
            ).all()
            if shifts:
                found_shifts = shifts
                source_week_start = test_week_start
                break

        if found_shifts:
            for s in found_shifts:
                day_offset = (s.start_time.date() - source_week_start).days
                new_start = datetime.combine(to_date + timedelta(days=day_offset), s.start_time.time())
                new_end = datetime.combine(to_date + timedelta(days=day_offset), s.end_time.time())
                if new_end <= new_start:
                    new_end += timedelta(days=1)
                new_items.append(OfficialShift(
                    branch_name=s.branch_name,
                    title=s.title,
                    description=s.description,
                    start_time=new_start,
                    end_time=new_end,
                    status='draft'
                ))

    if new_items:
        db.session.add_all(new_items)
        db.session.flush()
        log_action('insert', {'shift_ids': [item.id for item in new_items]})
        db.session.commit()
        return jsonify({"success": True, "message": f"Successfully copied last published weeks! Pasted {len(new_items)} shifts as drafts.", "count": len(new_items)})
    return jsonify({"success": True, "message": "No source shifts found to copy.", "count": 0})


# ==================================================================

@app.route('/api/shifts/apply_friday_pattern', methods=['POST'])
# Legacy alias kept so older clients keep working after the Thursday -> Friday switch
@app.route('/api/shifts/apply_thursday_pattern', methods=['POST'])
@login_required
def api_apply_friday_pattern():
    """Turn a normal working day into a holiday by cloning THIS WEEK'S FRIDAY roster onto it."""
    data = request.json
    branch_name = data.get('branch')
    target_date_str = data.get('target_date')
    week_start_str = data.get('week_start')

    if current_user.role == 'supervisor':
        branch_name = current_user.branch_name
        if not branch_name:
            return jsonify({'error': 'شعبه کاربر مشخص نیست'}), 400

    if not branch_name or not target_date_str or not week_start_str:
        return jsonify({'error': 'اطلاعات ناقص است.'}), 400

    try:
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'فرمت تاریخ نامعتبر است.'}), 400

    # Friday is day index 6 in a Saturday-start week
    friday_date = week_start + timedelta(days=6)

    if target_date == friday_date:
        return jsonify({'error': 'روز جمعه نمی‌تواند الگوی خودش را کپی کند.'}), 400

    # Match branch names (Farsi / English)
    lookup_names = _branch_lookup_names(branch_name)

    # 1. Fetch Friday shifts for this week
    fri_start = datetime.combine(friday_date, datetime.min.time())
    fri_end = datetime.combine(friday_date + timedelta(days=1), datetime.min.time())

    friday_shifts = OfficialShift.query.filter(
        OfficialShift.branch_name.in_(lookup_names),
        OfficialShift.start_time >= fri_start,
        OfficialShift.start_time < fri_end
    ).all()

    if not friday_shifts:
        return jsonify({'error': 'هیچ شیفتی برای جمعه این هفته تعریف نشده است.'}), 404

    # 2. Clear existing shifts on the target holiday date
    tgt_start = datetime.combine(target_date, datetime.min.time())
    tgt_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    OfficialShift.query.filter(
        OfficialShift.branch_name.in_(lookup_names),
        OfficialShift.start_time >= tgt_start,
        OfficialShift.start_time < tgt_end
    ).delete(synchronize_session=False)

    # 3. Copy Friday's shift template to target date (category included)
    new_shifts = []
    for s in friday_shifts:
        new_start = datetime.combine(target_date, s.start_time.time())
        duration = s.end_time - s.start_time
        new_end = new_start + duration

        new_shifts.append(OfficialShift(
            branch_name=s.branch_name,
            title=s.title,
            description="تعطیل (طرح جمعه)",
            start_time=new_start,
            end_time=new_end,
            status=s.status,
            category_id=s.category_id
        ))

    db.session.add_all(new_shifts)
    db.session.flush()

    if current_user.role == 'admin':
        log_action('insert', {'shift_ids': [item.id for item in new_shifts]})

    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'شیفت‌های جمعه با موفقیت به تاریخ {target_date_str} (تعطیل) منتقل شدند.',
        'count': len(new_shifts)
    })



# ==================================================================
@app.route('/api/weekly_schedule')
@login_required
def api_weekly_schedule():
    try:
        week_start_str = request.args.get('week_start')
        branch_param = request.args.get('branch', '').strip()

        # Determine branch based on role
        if current_user.role == 'supervisor':
            branch = current_user.branch_name
            if not branch:
                return jsonify({'error': 'هیچ شعبه‌ای برای حساب کاربری شما تعریف نشده است. لطفا با مدیر سیستم تماس بگیرید.'}), 400
        else:
            branch = branch_param if branch_param else None

        # Calculate week range (starts Saturday)
        if week_start_str:
            try:
                week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'فرمت تاریخ نامعتبر است.'}), 400
        else:
            today = datetime.now().date()
            # Python weekday: Mon=0 ... Sat=5, Sun=6
            days_back = (today.weekday() - 5) % 7
            week_start = today - timedelta(days=days_back)

        week_end = week_start + timedelta(days=6)

        # Build shift query
        q = OfficialShift.query.filter(
            OfficialShift.start_time >= datetime.combine(week_start, datetime.min.time()),
            OfficialShift.start_time < datetime.combine(week_end + timedelta(days=1), datetime.min.time())
        )

        # Branch filter
        branch_display = None
        branch_info_obj = None
        if branch:
            branch_info_obj = Branch.query.filter(
                or_(Branch.branch_name == branch, Branch.branch_name_farsi == branch)
            ).first()
            if branch_info_obj:
                branch_display = branch_info_obj.branch_name_farsi or branch_info_obj.branch_name
                q = q.filter(or_(
                    OfficialShift.branch_name == branch_info_obj.branch_name,
                    OfficialShift.branch_name == branch_info_obj.branch_name_farsi
                ))
            else:
                q = q.filter(OfficialShift.branch_name == branch)
                branch_display = branch
        else:
            branch_display = "همه شعب"

        # Supervisors only see approved shifts
        if current_user.role == 'supervisor':
            q = q.filter(OfficialShift.status == 'approved')

        shifts = q.order_by(OfficialShift.start_time).all()

        # Check if week is locked
        is_locked = False
        supervisor_note = ''
        if branch_display:
            b_en = branch_display
            b_fa = branch_display
            if branch_info_obj:
                b_en = branch_info_obj.branch_name
                b_fa = branch_info_obj.branch_name_farsi
            lock = BranchWeekLock.query.filter(
                BranchWeekLock.branch_name.in_([b_en, b_fa]),
                BranchWeekLock.week_start_date == week_start
            ).first()
            if lock:
                is_locked = True
                supervisor_note = (lock.supervisor_note or '').strip()

        # Group shifts by date and sort them chronologically
        shifts_by_day = {}
        for s in shifts:
            d_date = s.start_time.date()
            if d_date not in shifts_by_day:
                shifts_by_day[d_date] = []
            shifts_by_day[d_date].append(s)

        for d_date in shifts_by_day:
            shifts_by_day[d_date].sort(key=lambda x: x.start_time)

        # Helper to convert datetime to minutes from midnight
        def get_minutes(dt):
            return dt.hour * 60 + dt.minute

        def build_shift_columns(all_shifts):
            from collections import defaultdict

            TOLERANCE_MIN = 90  # max minutes apart to be considered the same column

            by_title = defaultdict(list)
            for s in all_shifts:
                by_title[s.title].append(s)

            clusters = []  # each: {'title', 'shifts': [...], 'sum_minutes', 'count'}

            for title, title_shifts in by_title.items():
                title_shifts_sorted = sorted(title_shifts, key=lambda s: get_minutes(s.start_time))
                title_clusters = []
                for s in title_shifts_sorted:
                    s_min = get_minutes(s.start_time)
                    placed = False
                    for c in title_clusters:
                        avg = c['sum_minutes'] / c['count']
                        if abs(avg - s_min) <= TOLERANCE_MIN:
                            c['shifts'].append(s)
                            c['sum_minutes'] += s_min
                            c['count'] += 1
                            placed = True
                            break
                    if not placed:
                        title_clusters.append({
                            'title': title,
                            'shifts': [s],
                            'sum_minutes': s_min,
                            'count': 1
                        })
                clusters.extend(title_clusters)

            # Sort all clusters (across all titles) chronologically by average start time
            clusters.sort(key=lambda c: c['sum_minutes'] / c['count'])

            cols = []
            col_map = {}
            for idx, c in enumerate(clusters):
                col_key = f"col_{idx}"
                rep = c['shifts'][0]  # earliest shift in this cluster, used for header display
                cols.append({
                    'key': col_key,
                    'title': c['title'],
                    'start': rep.start_time.strftime('%H:%M'),
                    'end': rep.end_time.strftime('%H:%M'),
                })
                for s in c['shifts']:
                    col_map[s.id] = col_key

            return cols, col_map

        # Build columns list using Saturday-Friday sequence mapping
        day_names = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه']
        sorted_dates = [week_start + timedelta(days=i) for i in range(7)]

        shift_columns, shift_to_col_key = build_shift_columns(shifts)

        # Employee shift counts for this week
        emp_shift_counts = {}
        for s in shifts:
            if s.assigned_employee_id:
                emp_shift_counts[s.assigned_employee_id] = emp_shift_counts.get(s.assigned_employee_id, 0) + 1

        # Build days
        days = []
        for i in range(7):
            day_date = week_start + timedelta(days=i)
            day_shifts = shifts_by_day.get(day_date, [])
            shift_list = []
            for s in day_shifts:
                col_key = shift_to_col_key.get(s.id, '')
                shift_list.append({
                    'id': s.id,
                    'col_key': col_key,
                    'title': s.title,
                    'start': s.start_time.strftime('%H:%M'),
                    'end': s.end_time.strftime('%H:%M'),
                    'assigned_employee_id': s.assigned_employee_id or '',
                    'assigned_employee_name': s.assigned_employee_name or '',
                    'status': s.status
                })
            days.append({
                'date': day_date.isoformat(),
                'day_name': day_names[i],
                'shifts': shift_list
            })

        # Get employees for this branch's department
        employees = []
        dept_id = None
        if current_user.role == 'supervisor':
            dept_id = current_user.department_id
            if not dept_id:
                return jsonify({'error': 'هیچ دپارتمانی برای حساب کاربری شما تعریف نشده است. لطفا با مدیر سیستم تماس بگیرید.'}), 400
        elif current_user.role == 'admin' and branch:
            b_en = branch_info_obj.branch_name if branch_info_obj else branch
            b_fa = branch_info_obj.branch_name_farsi if branch_info_obj else None
            names_to_check = [b_en] + ([b_fa] if b_fa else [])
            dept = Department.query.filter(Department.name.in_(names_to_check)).first()
            if dept:
                dept_id = dept.id

        if dept_id:
            emps = AuthEmployee.query.filter(
                AuthEmployee.department_id == dept_id,
                AuthEmployee.role.in_(['barista', 'supervisor'])
            ).all()
            employees = sorted([{
                'id': e.employee_number,
                'name': e.full_name,
                'shift_count': emp_shift_counts.get(e.employee_number, 0)
            } for e in emps], key=lambda x: x['shift_count'])
        elif current_user.role == 'admin':
            emps = AuthEmployee.query.filter(AuthEmployee.role.in_(['barista', 'supervisor'])).all()
            employees = sorted([{
                'id': e.employee_number,
                'name': e.full_name,
                'shift_count': emp_shift_counts.get(e.employee_number, 0)
            } for e in emps], key=lambda x: x['shift_count'])

        # Get deadline for this week range
        dl = ShiftDeadline.query.filter(
            ShiftDeadline.period_start <= week_end,
            ShiftDeadline.period_end >= week_start
        ).order_by(ShiftDeadline.id.desc()).first()
        deadline_info = dl.deadline_datetime.isoformat() if dl else None

        return jsonify({
            'week_start': week_start.isoformat(),
            'week_end': week_end.isoformat(),
            'branch': branch_display,
            'shift_columns': shift_columns,
            'days': days,
            'employees': employees,
            'deadline': deadline_info,
            'role': current_user.role,
            'is_locked': is_locked,
            'supervisor_note': supervisor_note
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'خطای سرور: {str(e)}'}), 500

@app.route('/api/shifts/<int:id>/admin_assign', methods=['POST'])
@login_required
@role_required('admin')
def api_admin_assign_shift(id):
    shift = OfficialShift.query.get_or_404(id)
    data = request.json
    emp_id = data.get('emp_id', '').strip()
    emp_name = data.get('emp_name', '').strip()

    shift.assigned_employee_id = emp_id or None
    shift.assigned_employee_name = emp_name or None
    shift.supervisor_id = 'admin' if emp_id else None
    shift.supervisor_name = 'مدیر' if emp_id else None

    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/import_csv', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_import_csv():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('No file selected.', 'error')
            return redirect(request.url)
        if file.filename.lower().endswith('.csv'):
            try:
                import csv
                from io import StringIO
                stream = StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
                reader = csv.DictReader(stream)
                reader.fieldnames = [c.strip().replace('\ufeff', '') for c in reader.fieldnames]
                rows = list(reader)
                if not rows:
                    flash('CSV file is empty', 'error')
                    return redirect(request.url)
                dates = []
                branches_in_csv = set()
                objects = []
                for row in rows:
                    try:
                        invoice_date = datetime.strptime(row['InvoiceDate'].strip(), '%Y-%m-%d').date()
                        dates.append(invoice_date)
                        branches_in_csv.add(row['BranchName'].strip())
                        total_invoices = int(float(row['TotalInvoices'])) if row.get('TotalInvoices', '').strip() else 0
                        total_items = float(row['TotalItems']) if row.get('TotalItems', '').strip() else 0.0
                        total_minutes = float(row['TotalMinutesInHour']) if row.get('TotalMinutesInHour', '').strip() else 0.0
                        invoice_hour = int(float(row['InvoiceHour'])) if row.get('InvoiceHour', '').strip() else 0
                        objects.append(BranchInvoiceStats(
                            branchname=row['BranchName'].strip(),
                            invoicedate=invoice_date,
                            invoicehour=invoice_hour,
                            totalinvoices=total_invoices,
                            totalitems=total_items,
                            totalminutesinhour=total_minutes,
                        ))
                    except (ValueError, KeyError) as e:
                        flash(f'Error parsing row: {e}', 'error')
                        return redirect(request.url)

                if dates:
                    min_date = min(dates)
                    max_date = max(dates)
                    existing_rows = BranchInvoiceStats.query.filter(
                        BranchInvoiceStats.branchname.in_(branches_in_csv),
                        BranchInvoiceStats.invoicedate.between(min_date, max_date)
                    ).all()
                    existing_map = {(row.branchname, row.invoicedate, row.invoicehour): row for row in existing_rows}
                    updated = 0
                    inserted = 0
                    new_objects = []
                    for obj in objects:
                        key = (obj.branchname, obj.invoicedate, obj.invoicehour)
                        existing = existing_map.get(key)
                        if existing:
                            existing.totalinvoices = obj.totalinvoices
                            existing.totalitems = obj.totalitems
                            existing.totalminutesinhour = obj.totalminutesinhour
                            updated += 1
                        else:
                            new_objects.append(obj)
                            inserted += 1
                    if new_objects:
                        db.session.bulk_save_objects(new_objects)
                    db.session.commit()
                    flash(f'Import complete: {inserted} new rows, {updated} updated.', 'success')
                else:
                    flash('No valid dates found in CSV', 'error')
                return redirect(url_for('admin_calendar'))
            except Exception as e:
                db.session.rollback()
                flash(f'Error processing file: {e}', 'error')
    return render_template('import_csv.html')

@app.route("/contact")
def contact():
    try:
        return render_template("contact.html")
    except Exception as e:
        import traceback
        return f"<pre>{traceback.format_exc()}</pre>", 500

def init_db():
    with app.app_context():
        db.create_all()
        ensure_schema_upgrades()
        
        # Create default admin if not exists
        if not AppAdmin.query.filter_by(username='admin').first():
            db.session.add(AppAdmin(username='admin', password_hash=generate_password_hash('12345', method='pbkdf2:sha256')))
            db.session.commit()

        # Seed BranchSupervisorPassword table ONLY from active supervisors (excluding dept 68) with valid passwords
        existing_supervisors = AuthEmployee.query.filter(
            AuthEmployee.role == 'supervisor',
            AuthEmployee.department_id != 68,
            AuthEmployee.password_text.isnot(None)
        ).all()

        for sup in existing_supervisors:
            if sup.department_id and sup.password_hash and sup.password_text:
                # Use db.session.get() to avoid SQLAlchemy 2.0 deprecation warning
                bp = db.session.get(BranchSupervisorPassword, sup.department_id)
                if not bp:
                    db.session.add(BranchSupervisorPassword(
                        department_id=sup.department_id,
                        password_hash=sup.password_hash,
                        password_text=sup.password_text
                    ))
                    
        db.session.commit()

@app.route('/barista')
@login_required
@role_required('barista')
def barista_dashboard():
    # Calculate the start of the current week (Saturday)
    today = datetime.now().date()
    days_back = (today.weekday() - 5) % 7
    week_start = today - timedelta(days=days_back)
    week_end = week_start + timedelta(days=6)

    # Fetch all shifts assigned to the current barista
    all_assigned = OfficialShift.query.filter(
        OfficialShift.assigned_employee_id == current_user.username
    ).order_by(OfficialShift.start_time.desc()).all()

    upcoming_shifts = []
    past_shifts = []
    total_hours_this_week = 0.0
    total_hours_all_time = 0.0

    for s in all_assigned:
        duration_hours = (s.end_time - s.start_time).total_seconds() / 3600.0
        total_hours_all_time += duration_hours
        
        shift_data = {
            'id': s.id,
            'title': s.title,
            'branch': s.branch_name,
            'start': s.start_time,
            'end': s.end_time,
            'date_str': s.start_time.date().isoformat(),
            'duration': round(duration_hours, 1)
        }

        # Divide into upcoming (this week and future) vs past shifts
        if s.start_time.date() >= week_start:
            upcoming_shifts.append(shift_data)
            if s.start_time.date() <= week_end:
                total_hours_this_week += duration_hours
        else:
            past_shifts.append(shift_data)

    # Sort upcoming shifts chronologically
    upcoming_shifts.sort(key=lambda x: x['start'])

    return render_template(
        'barista_dashboard.html',
        upcoming_shifts=upcoming_shifts,
        past_shifts=past_shifts,
        total_hours_this_week=round(total_hours_this_week, 1),
        total_hours_all_time=round(total_hours_all_time, 1),
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat()
    )

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=4343, debug=False)