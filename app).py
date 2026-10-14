"""Hostel Accommodation & Student Services Management System
Run:  pip install flask  &&  python app.py   ->  http://127.0.0.1:5000
DB (SQLite) is auto-created with schema, constraints, triggers, views, indexes and sample data.
"""
import sqlite3, re, os
from datetime import date, datetime
from flask import Flask, request, redirect, flash, render_template_string, g

app = Flask(__name__)
app.secret_key = 'hostel-demo'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hostel.db')

SCHEMA = """
CREATE TABLE block(block_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  gender TEXT NOT NULL CHECK(gender IN('M','F')), warden TEXT NOT NULL);
CREATE TABLE room(room_id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL REFERENCES block,
  room_no TEXT NOT NULL, floor INTEGER NOT NULL CHECK(floor>=0),
  capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 6), UNIQUE(block_id,room_no));
CREATE TABLE bed(bed_id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES room,
  bed_no INTEGER NOT NULL, UNIQUE(room_id,bed_no));
CREATE TABLE student(student_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  gender TEXT NOT NULL CHECK(gender IN('M','F')), email TEXT NOT NULL UNIQUE,
  phone TEXT NOT NULL CHECK(length(phone)=10), dept TEXT NOT NULL,
  year INTEGER NOT NULL CHECK(year BETWEEN 1 AND 5), joined DATE NOT NULL DEFAULT CURRENT_DATE);
CREATE TABLE allocation(alloc_id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student,
  bed_id INTEGER NOT NULL REFERENCES bed, start_date DATE NOT NULL DEFAULT CURRENT_DATE, end_date DATE,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN('ACTIVE','ENDED')),
  CHECK(end_date IS NULL OR end_date>=start_date),
  CHECK((status='ACTIVE' AND end_date IS NULL) OR status='ENDED'));
CREATE UNIQUE INDEX ux_one_active_bed ON allocation(bed_id) WHERE status='ACTIVE';
CREATE UNIQUE INDEX ux_one_active_student ON allocation(student_id) WHERE status='ACTIVE';
CREATE TABLE transfer(transfer_id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student,
  from_bed INTEGER NOT NULL REFERENCES bed, to_bed INTEGER NOT NULL REFERENCES bed,
  transfer_date DATE NOT NULL DEFAULT CURRENT_DATE, reason TEXT NOT NULL, CHECK(from_bed<>to_bed));
CREATE TABLE vacating(vacate_id INTEGER PRIMARY KEY, alloc_id INTEGER NOT NULL UNIQUE REFERENCES allocation,
  vacate_date DATE NOT NULL DEFAULT CURRENT_DATE, reason TEXT NOT NULL);
CREATE TABLE fee_schedule(fee_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  amount REAL NOT NULL CHECK(amount>0), due_date DATE NOT NULL);
CREATE TABLE payment(payment_id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student,
  fee_id INTEGER NOT NULL REFERENCES fee_schedule, amount REAL NOT NULL CHECK(amount>0),
  paid_on DATE NOT NULL DEFAULT CURRENT_DATE, mode TEXT NOT NULL DEFAULT 'CASH' CHECK(mode IN('CASH','UPI','CARD','BANK')));
CREATE TABLE visitor(visitor_id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student,
  name TEXT NOT NULL, relation TEXT NOT NULL, in_time TEXT NOT NULL, out_time TEXT,
  CHECK(out_time IS NULL OR out_time>=in_time));
CREATE TABLE complaint(complaint_id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES student,
  category TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN'
  CHECK(status IN('OPEN','IN_PROGRESS','RESOLVED')), created DATE NOT NULL DEFAULT CURRENT_DATE);
CREATE TABLE maintenance(req_id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES room,
  issue TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN('PENDING','IN_PROGRESS','DONE')),
  reported_on DATE NOT NULL DEFAULT CURRENT_DATE, resolved_on DATE, CHECK(resolved_on IS NULL OR resolved_on>=reported_on));
CREATE INDEX ix_student_name ON student(name);
CREATE INDEX ix_pay_student ON payment(student_id,fee_id);
CREATE INDEX ix_visitor_student ON visitor(student_id);
CREATE INDEX ix_complaint_status ON complaint(status);
CREATE INDEX ix_alloc_student ON allocation(student_id);

CREATE TRIGGER trg_pay_limit BEFORE INSERT ON payment BEGIN
  SELECT RAISE(ABORT,'Payment exceeds the fee amount due')
  WHERE (SELECT COALESCE(SUM(amount),0) FROM payment WHERE student_id=NEW.student_id AND fee_id=NEW.fee_id)+NEW.amount
        > (SELECT amount FROM fee_schedule WHERE fee_id=NEW.fee_id); END;
CREATE TRIGGER trg_alloc_gender BEFORE INSERT ON allocation BEGIN
  SELECT RAISE(ABORT,'Student gender does not match block gender')
  WHERE (SELECT bl.gender FROM bed b JOIN room r ON r.room_id=b.room_id JOIN block bl ON bl.block_id=r.block_id WHERE b.bed_id=NEW.bed_id)
     <> (SELECT gender FROM student WHERE student_id=NEW.student_id); END;
CREATE TRIGGER trg_bed_capacity BEFORE INSERT ON bed BEGIN
  SELECT RAISE(ABORT,'Room capacity exceeded: cannot add more beds')
  WHERE (SELECT COUNT(*) FROM bed WHERE room_id=NEW.room_id) >= (SELECT capacity FROM room WHERE room_id=NEW.room_id); END;

CREATE VIEW v_vacant_beds AS SELECT b.bed_id, bl.name block, r.room_no, b.bed_no, bl.gender
  FROM bed b JOIN room r ON r.room_id=b.room_id JOIN block bl ON bl.block_id=r.block_id
  WHERE NOT EXISTS(SELECT 1 FROM allocation a WHERE a.bed_id=b.bed_id AND a.status='ACTIVE');
CREATE VIEW v_occupancy AS SELECT bl.name block, COUNT(b.bed_id) total_beds,
  COUNT(a.alloc_id) occupied, COUNT(b.bed_id)-COUNT(a.alloc_id) vacant,
  ROUND(100.0*COUNT(a.alloc_id)/COUNT(b.bed_id),1) occupancy_pct
  FROM block bl JOIN room r ON r.block_id=bl.block_id JOIN bed b ON b.room_id=r.room_id
  LEFT JOIN allocation a ON a.bed_id=b.bed_id AND a.status='ACTIVE' GROUP BY bl.block_id;
CREATE VIEW v_fee_dues AS SELECT s.student_id, s.name student, f.name fee, f.amount,
  COALESCE(SUM(p.amount),0) paid, f.amount-COALESCE(SUM(p.amount),0) due, f.due_date
  FROM student s CROSS JOIN fee_schedule f LEFT JOIN payment p ON p.student_id=s.student_id AND p.fee_id=f.fee_id
  GROUP BY s.student_id, f.fee_id;
"""

def seed(c):
    for n, gd, w in [('Block A', 'M', 'Mr. Rao'), ('Block B', 'F', 'Ms. Sharma')]:
        c.execute('INSERT INTO block(name,gender,warden) VALUES(?,?,?)', (n, gd, w))
    for bid in (1, 2):
        for fl, rn in [(1, '101'), (1, '102'), (2, '201'), (2, '202')]:
            rid = c.execute('INSERT INTO room(block_id,room_no,floor,capacity) VALUES(?,?,?,3)', (bid, rn, fl)).lastrowid
            for k in (1, 2, 3):
                c.execute('INSERT INTO bed(room_id,bed_no) VALUES(?,?)', (rid, k))
    studs = [('Arjun Reddy', 'M', 'CSE', 2), ('Rahul Verma', 'M', 'ECE', 1), ('Kiran Kumar', 'M', 'MECH', 3),
             ('Sai Teja', 'M', 'CSE', 4), ('Ananya Iyer', 'F', 'IT', 2), ('Priya Singh', 'F', 'CSE', 1),
             ('Meera Nair', 'F', 'EEE', 3), ('Divya Rao', 'F', 'ECE', 2), ('Vikram Patel', 'M', 'CIVIL', 1),
             ('Neha Gupta', 'F', 'IT', 1)]
    for i, (n, gd, d, y) in enumerate(studs):
        c.execute('INSERT INTO student(name,gender,email,phone,dept,year) VALUES(?,?,?,?,?,?)',
                  (n, gd, n.split()[0].lower() + '@college.edu', '98765432%02d' % (10 + i), d, y))
    for sid in range(1, 9):
        gd = studs[sid - 1][1]
        bed = c.execute('SELECT bed_id FROM v_vacant_beds WHERE gender=? ORDER BY bed_id LIMIT 1', (gd,)).fetchone()[0]
        c.execute("INSERT INTO allocation(student_id,bed_id,start_date) VALUES(?,?,'2026-07-15')", (sid, bed))
    for n, a, d in [('Hostel Rent Sem1', 30000, '2026-08-31'), ('Mess Fee Sem1', 18000, '2026-08-31'), ('Security Deposit', 5000, '2026-07-31')]:
        c.execute('INSERT INTO fee_schedule(name,amount,due_date) VALUES(?,?,?)', (n, a, d))
    for sid, fid, amt, m in [(1, 1, 30000, 'UPI'), (1, 3, 5000, 'CASH'), (2, 1, 15000, 'CARD'), (5, 1, 30000, 'BANK'), (5, 2, 18000, 'UPI'), (3, 3, 5000, 'CASH')]:
        c.execute('INSERT INTO payment(student_id,fee_id,amount,mode) VALUES(?,?,?,?)', (sid, fid, amt, m))
    c.execute("INSERT INTO visitor(student_id,name,relation,in_time,out_time) VALUES(1,'Ramesh Reddy','Father','2026-10-10 10:00','2026-10-10 12:00')")
    c.execute("INSERT INTO visitor(student_id,name,relation,in_time) VALUES(5,'Lakshmi Iyer','Mother','2026-10-13 09:30')")
    c.execute("INSERT INTO complaint(student_id,category,description) VALUES(2,'Water','No hot water on 1st floor')")
    c.execute("INSERT INTO complaint(student_id,category,description,status) VALUES(6,'WiFi','Router down in room','IN_PROGRESS')")
    c.execute("INSERT INTO complaint(student_id,category,description,status) VALUES(1,'Noise','Late-night noise','RESOLVED')")
    c.execute("INSERT INTO maintenance(room_id,issue) VALUES(1,'Ceiling fan not working')")
    c.execute("INSERT INTO maintenance(room_id,issue,status) VALUES(6,'Leaking tap','IN_PROGRESS')")

def init():
    if not os.path.exists(DB):
        c = sqlite3.connect(DB)
        c.executescript(SCHEMA); seed(c); c.commit(); c.close()

def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop('db', None)
    if d: d.close()

def q(sql, a=()): return db().execute(sql, a).fetchall()
def today(): return date.today().isoformat()
def now(): return datetime.now().strftime('%Y-%m-%d %H:%M')

def v(f, k, label=None):
    x = f.get(k, '').strip()
    if not x: raise ValueError(f'{label or k} is required')
    return x

def vdate(x, label):
    try: return datetime.strptime(x, '%Y-%m-%d').date().isoformat()
    except ValueError: raise ValueError(f'{label} must be a valid date (YYYY-MM-DD)')

NAV = [('/', 'Dashboard'), ('/students', 'Students'), ('/allocate', 'Allocation'), ('/transfer', 'Transfer'),
       ('/fees', 'Fees'), ('/visitors', 'Visitors'), ('/complaints', 'Complaints'), ('/maintenance', 'Maintenance'), ('/reports', 'Reports')]

BASE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{{title}} - Hostel</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f9;color:#1d2433}
nav{background:#1f3a5f;padding:10px 16px;display:flex;flex-wrap:wrap;gap:4px}
nav a{color:#cfe0f5;text-decoration:none;padding:6px 12px;border-radius:6px}nav a.on,nav a:hover{background:#2f5a94;color:#fff}
main{max-width:1100px;margin:20px auto;padding:0 16px}h2{margin-top:0}
.card{background:#fff;border-radius:10px;padding:16px;margin-bottom:18px;box-shadow:0 1px 3px #0002}
form{display:flex;flex-wrap:wrap;gap:10px;align-items:end}label{font-size:12px;color:#556;display:flex;flex-direction:column;gap:3px}
input,select,textarea{padding:7px;border:1px solid #bcc5d3;border-radius:6px;min-width:150px}
button{background:#2f5a94;color:#fff;border:0;padding:9px 16px;border-radius:6px;cursor:pointer}button:hover{background:#1f3a5f}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:7px 10px;border-bottom:1px solid #e3e8ef;text-align:left;white-space:nowrap}
th{background:#eef2f8}.msg{padding:10px 14px;border-radius:6px;margin-bottom:12px}.ok{background:#d9f5e3;color:#14592f}.err{background:#fde0e0;color:#8a1c1c}
h3{margin:0 0 10px;font-size:16px;width:100%}.empty{color:#889}</style></head><body>
<nav>{% for u,n in nav %}<a href="{{u}}" class="{{'on' if u==path}}">{{n}}</a>{% endfor %}</nav><main><h2>{{title}}</h2>
{% for c,m in get_flashed_messages(with_categories=true) %}<div class="msg {{c}}">{{m}}</div>{% endfor %}
{% if search %}<div class=card><form method=get><label>Search name / dept / email<input name=q value="{{request.args.get('q','')}}"></label><button>Search</button></form></div>{% endif %}
{% for fm in forms %}<div class=card><form method=post><h3>{{fm.t}}</h3><input type=hidden name=_k value="{{fm.k}}">
{% for n,l,kind,o in fm.f %}<label>{{l}}
{% if kind=='select' %}<select name="{{n}}" required>{% for val,lab in o %}<option value="{{val}}">{{lab}}</option>{% endfor %}</select>
{% elif kind=='area' %}<textarea name="{{n}}" required></textarea>
{% else %}<input name="{{n}}" type="{{kind}}" value="{{o or ''}}" required>{% endif %}</label>{% endfor %}
<button>Submit</button></form></div>{% endfor %}
{% for t,rows in tables %}<div class=card><h3>{{t}}</h3><div class=tw>{% if rows %}<table><tr>{% for k in rows[0].keys() %}<th>{{k}}</th>{% endfor %}</tr>
{% for r in rows %}<tr>{% for k in r.keys() %}<td>{{r[k]}}</td>{% endfor %}</tr>{% endfor %}</table>{% else %}<span class=empty>No records</span>{% endif %}</div></div>{% endfor %}
</main></body></html>"""

def F(k, t, fields): return dict(k=k, t=t, f=fields)

def view(title, forms=(), tables=(), do=None, search=False):
    if request.method == 'POST':
        try:
            do(request.form['_k'], request.form)
            db().commit(); flash('Operation successful', 'ok')
        except (sqlite3.IntegrityError, ValueError) as e:
            db().rollback(); flash(str(e), 'err')
        return redirect(request.full_path.rstrip('?'))
    return render_template_string(BASE, title=title, forms=forms, tables=tables, nav=NAV, path=request.path, search=search)

def opts(sql): return [(r[0], r[1]) for r in q(sql)]
BEDS = lambda: opts("SELECT bed_id, block||' / Room '||room_no||' / Bed '||bed_no||' ('||gender||')' FROM v_vacant_beds")
UNALLOC = lambda: opts("SELECT student_id, student_id||' - '||name FROM student WHERE student_id NOT IN(SELECT student_id FROM allocation WHERE status='ACTIVE')")
ALLOCD = lambda: opts("SELECT student_id, student_id||' - '||name FROM student WHERE student_id IN(SELECT student_id FROM allocation WHERE status='ACTIVE')")
ALLS = lambda: opts("SELECT student_id, student_id||' - '||name FROM student")

@app.route('/')
def home():
    return view('Dashboard - Occupancy & Vacancies', tables=[
        ('Occupancy by block (view v_occupancy)', q('SELECT * FROM v_occupancy')),
        ('Vacant beds (view v_vacant_beds)', q('SELECT * FROM v_vacant_beds')),
        ('Totals', q("SELECT (SELECT COUNT(*) FROM student) students,(SELECT COUNT(*) FROM allocation WHERE status='ACTIVE') residents,"
                     "(SELECT COUNT(*) FROM complaint WHERE status<>'RESOLVED') open_complaints,(SELECT COUNT(*) FROM maintenance WHERE status<>'DONE') pending_maintenance")) ])

@app.route('/students', methods=['GET', 'POST'])
def students():
    def do(k, f):
        if k == 'add':
            email, ph = v(f, 'email').lower(), v(f, 'phone')
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email): raise ValueError('Invalid email format')
            if not re.fullmatch(r'\d{10}', ph): raise ValueError('Phone must be exactly 10 digits')
            db().execute('INSERT INTO student(name,gender,email,phone,dept,year) VALUES(?,?,?,?,?,?)',
                         (v(f, 'name'), f['gender'], email, ph, v(f, 'dept'), int(v(f, 'year'))))
        elif k == 'upd':
            ph = v(f, 'phone')
            if not re.fullmatch(r'\d{10}', ph): raise ValueError('Phone must be exactly 10 digits')
            db().execute('UPDATE student SET phone=? WHERE student_id=?', (ph, f['sid']))
        else:
            db().execute('DELETE FROM student WHERE student_id=?', (f['sid'],))
    s = '%' + request.args.get('q', '') + '%'
    return view('Student Registration', [
        F('add', 'Register student', [('name', 'Name', 'text', ''), ('gender', 'Gender', 'select', [('M', 'Male'), ('F', 'Female')]),
          ('email', 'Email', 'email', ''), ('phone', '10-digit phone', 'text', ''), ('dept', 'Department', 'text', ''), ('year', 'Year', 'number', 1)]),
        F('upd', 'Update phone', [('sid', 'Student', 'select', ALLS()), ('phone', 'New phone', 'text', '')]),
        F('del', 'Delete student (only if no records)', [('sid', 'Student', 'select', ALLS())])],
        [('Students', q("SELECT s.student_id id,s.name,s.gender,s.email,s.phone,s.dept,s.year,"
                        "(SELECT bl.name||' '||r.room_no||'/B'||b.bed_no FROM allocation a JOIN bed b ON b.bed_id=a.bed_id JOIN room r ON r.room_id=b.room_id "
                        "JOIN block bl ON bl.block_id=r.block_id WHERE a.student_id=s.student_id AND a.status='ACTIVE') current_bed "
                        "FROM student s WHERE name LIKE ? OR dept LIKE ? OR email LIKE ? ORDER BY id", (s, s, s)))], do, search=True)

@app.route('/allocate', methods=['GET', 'POST'])
def allocate():
    def do(k, f):
        if k == 'al':
            db().execute('INSERT INTO allocation(student_id,bed_id,start_date) VALUES(?,?,?)', (f['sid'], f['bed'], vdate(v(f, 'start'), 'Start date')))
        else:
            a = db().execute("SELECT alloc_id,start_date FROM allocation WHERE student_id=? AND status='ACTIVE'", (f['sid'],)).fetchone()
            d = vdate(v(f, 'date'), 'Vacating date')
            if d < a['start_date']: raise ValueError('Vacating date cannot be before allocation start date')
            db().execute("UPDATE allocation SET status='ENDED', end_date=? WHERE alloc_id=?", (d, a['alloc_id']))
            db().execute('INSERT INTO vacating(alloc_id,vacate_date,reason) VALUES(?,?,?)', (a['alloc_id'], d, v(f, 'reason')))
    return view('Bed Allocation & Vacating', [
        F('al', 'Allocate bed', [('sid', 'Student (unallocated)', 'select', UNALLOC()), ('bed', 'Vacant bed', 'select', BEDS()), ('start', 'Start date', 'date', today())]),
        F('vc', 'Vacate bed', [('sid', 'Resident', 'select', ALLOCD()), ('date', 'Vacating date', 'date', today()), ('reason', 'Reason', 'text', '')])],
        [('Allocation history', q("SELECT a.alloc_id,s.name,bl.name block,r.room_no,b.bed_no,a.start_date,a.end_date,a.status FROM allocation a JOIN student s ON s.student_id=a.student_id "
                                  "JOIN bed b ON b.bed_id=a.bed_id JOIN room r ON r.room_id=b.room_id JOIN block bl ON bl.block_id=r.block_id ORDER BY a.alloc_id DESC"))], do)

@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    def do(k, f):
        a = db().execute("SELECT alloc_id,bed_id,start_date FROM allocation WHERE student_id=? AND status='ACTIVE'", (f['sid'],)).fetchone()
        d = vdate(v(f, 'date'), 'Transfer date')
        if d < a['start_date']: raise ValueError('Transfer date cannot be before allocation start date')
        db().execute("UPDATE allocation SET status='ENDED', end_date=? WHERE alloc_id=?", (d, a['alloc_id']))
        db().execute('INSERT INTO allocation(student_id,bed_id,start_date) VALUES(?,?,?)', (f['sid'], f['bed'], d))
        db().execute('INSERT INTO transfer(student_id,from_bed,to_bed,transfer_date,reason) VALUES(?,?,?,?,?)', (f['sid'], a['bed_id'], f['bed'], d, v(f, 'reason')))
    return view('Room / Bed Transfer (atomic transaction)', [
        F('t', 'Transfer resident', [('sid', 'Resident', 'select', ALLOCD()), ('bed', 'New vacant bed', 'select', BEDS()), ('date', 'Date', 'date', today()), ('reason', 'Reason', 'text', '')])],
        [('Transfer log', q("SELECT t.transfer_id,s.name,t.from_bed,t.to_bed,t.transfer_date,t.reason FROM transfer t JOIN student s ON s.student_id=t.student_id ORDER BY 1 DESC"))], do)

@app.route('/fees', methods=['GET', 'POST'])
def fees():
    def do(k, f):
        if k == 'pay':
            db().execute('INSERT INTO payment(student_id,fee_id,amount,mode,paid_on) VALUES(?,?,?,?,?)',
                         (f['sid'], f['fee'], float(v(f, 'amt', 'Amount')), f['mode'], vdate(v(f, 'date'), 'Payment date')))
        else:
            db().execute('INSERT INTO fee_schedule(name,amount,due_date) VALUES(?,?,?)', (v(f, 'name'), float(v(f, 'amt')), vdate(v(f, 'due'), 'Due date')))
    return view('Fee Collection', [
        F('pay', 'Collect payment', [('sid', 'Student', 'select', ALLS()), ('fee', 'Fee', 'select', opts("SELECT fee_id,name||' ('||amount||')' FROM fee_schedule")),
          ('amt', 'Amount', 'number', ''), ('mode', 'Mode', 'select', [(m, m) for m in ('CASH', 'UPI', 'CARD', 'BANK')]), ('date', 'Date', 'date', today())]),
        F('fee', 'Add fee schedule', [('name', 'Fee name', 'text', ''), ('amt', 'Amount', 'number', ''), ('due', 'Due date', 'date', today())])],
        [('Fee dues (view v_fee_dues)', q('SELECT * FROM v_fee_dues WHERE due>0 ORDER BY student_id')),
         ('Recent payments', q('SELECT p.payment_id,s.name,f.name fee,p.amount,p.mode,p.paid_on FROM payment p JOIN student s ON s.student_id=p.student_id JOIN fee_schedule f ON f.fee_id=p.fee_id ORDER BY 1 DESC LIMIT 15'))], do)

@app.route('/visitors', methods=['GET', 'POST'])
def visitors():
    def do(k, f):
        if k == 'in':
            db().execute('INSERT INTO visitor(student_id,name,relation,in_time) VALUES(?,?,?,?)', (f['sid'], v(f, 'name'), v(f, 'rel'), now()))
        else:
            db().execute('UPDATE visitor SET out_time=? WHERE visitor_id=?', (now(), f['vid']))
    return view('Visitor Entry', [
        F('in', 'Visitor check-in', [('sid', 'Visiting student', 'select', ALLS()), ('name', 'Visitor name', 'text', ''), ('rel', 'Relation', 'text', '')]),
        F('out', 'Check-out', [('vid', 'Inside visitor', 'select', opts("SELECT visitor_id, name FROM visitor WHERE out_time IS NULL"))])],
        [('Visitor log', q('SELECT v.visitor_id,v.name visitor,v.relation,s.name student,v.in_time,v.out_time FROM visitor v JOIN student s ON s.student_id=v.student_id ORDER BY 1 DESC'))], do)

@app.route('/complaints', methods=['GET', 'POST'])
def complaints():
    def do(k, f):
        if k == 'new':
            db().execute('INSERT INTO complaint(student_id,category,description) VALUES(?,?,?)', (f['sid'], f['cat'], v(f, 'desc', 'Description')))
        else:
            db().execute('UPDATE complaint SET status=? WHERE complaint_id=?', (f['st'], f['cid']))
    return view('Complaint Tracking', [
        F('new', 'Register complaint', [('sid', 'Student', 'select', ALLS()), ('cat', 'Category', 'select', [(c, c) for c in ('Water', 'Electricity', 'WiFi', 'Food', 'Noise', 'Other')]), ('desc', 'Description', 'area', '')]),
        F('upd', 'Update status', [('cid', 'Complaint', 'select', opts("SELECT complaint_id,'#'||complaint_id||' '||category||' ('||status||')' FROM complaint")), ('st', 'Status', 'select', [(s, s) for s in ('OPEN', 'IN_PROGRESS', 'RESOLVED')])])],
        [('Complaints', q('SELECT c.complaint_id,s.name,c.category,c.description,c.status,c.created FROM complaint c JOIN student s ON s.student_id=c.student_id ORDER BY (c.status="RESOLVED"),1 DESC'))], do)

@app.route('/maintenance', methods=['GET', 'POST'])
def maintenance():
    def do(k, f):
        if k == 'new':
            db().execute('INSERT INTO maintenance(room_id,issue) VALUES(?,?)', (f['room'], v(f, 'issue')))
        else:
            db().execute("UPDATE maintenance SET status=?, resolved_on=CASE WHEN ?='DONE' THEN ? END WHERE req_id=?", (f['st'], f['st'], today(), f['rid']))
    rooms = opts("SELECT r.room_id, bl.name||' / '||r.room_no FROM room r JOIN block bl ON bl.block_id=r.block_id")
    return view('Maintenance Requests', [
        F('new', 'Raise request', [('room', 'Room', 'select', rooms), ('issue', 'Issue', 'text', '')]),
        F('upd', 'Update status', [('rid', 'Request', 'select', opts("SELECT req_id,'#'||req_id||' '||issue FROM maintenance")), ('st', 'Status', 'select', [(s, s) for s in ('PENDING', 'IN_PROGRESS', 'DONE')])])],
        [('Maintenance status', q('SELECT m.req_id,bl.name block,r.room_no,m.issue,m.status,m.reported_on,m.resolved_on FROM maintenance m JOIN room r ON r.room_id=m.room_id JOIN block bl ON bl.block_id=r.block_id ORDER BY (m.status="DONE"),1 DESC'))], do)

@app.route('/reports')
def reports():
    return view('Reports', tables=[
        ('Occupancy per block (GROUP BY / aggregates)', q('SELECT * FROM v_occupancy')),
        ('Rooms with vacancies (HAVING)', q("SELECT bl.name block,r.room_no,r.capacity,COUNT(a.alloc_id) occupied,r.capacity-COUNT(a.alloc_id) vacant FROM room r JOIN block bl ON bl.block_id=r.block_id JOIN bed b ON b.room_id=r.room_id "
                                          "LEFT JOIN allocation a ON a.bed_id=b.bed_id AND a.status='ACTIVE' GROUP BY r.room_id HAVING vacant>0")),
        ('Students with fee dues (nested query)', q("SELECT name,(SELECT SUM(due) FROM v_fee_dues d WHERE d.student_id=s.student_id) total_due FROM student s WHERE student_id IN(SELECT student_id FROM v_fee_dues WHERE due>0) ORDER BY total_due DESC")),
        ('Students without a bed (nested query)', q("SELECT student_id,name,dept FROM student WHERE student_id NOT IN(SELECT student_id FROM allocation WHERE status='ACTIVE')")),
        ('Unresolved complaints', q("SELECT c.complaint_id,s.name,c.category,c.status,c.created FROM complaint c JOIN student s ON s.student_id=c.student_id WHERE c.status<>'RESOLVED'")),
        ('Maintenance summary by status', q('SELECT status,COUNT(*) requests FROM maintenance GROUP BY status')),
        ('Visitors currently inside', q('SELECT v.name,v.relation,s.name student,v.in_time FROM visitor v JOIN student s ON s.student_id=v.student_id WHERE v.out_time IS NULL'))])

if __name__ == '__main__':
    init()
    app.run(debug=True)
