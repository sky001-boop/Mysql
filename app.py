from flask import Flask, render_template, redirect, request, session
import pymysql
import os
from werkzeug.utils import secure_filename
from datetime import date

app = Flask(__name__)
# 必须设置secret_key才能使用session（随便写一个字符串即可）
app.secret_key = 'campus_trade_secret_key_2026'


# 【新增】配置上传文件夹
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 【新增】检查文件类型的辅助函数
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
def get_conn():
    return pymysql.connect(
        host='mysql.railway.internal',
        user='root',
        password='tDRHNBIvWiEJUsKEJQGLvIiFXFoCDFTW',
        database='railway',
        port=3306,
        charset='utf8mb4'
    )

# 首页
# 首页（自动加载最新4个商品）
# 首页（支持商品分类筛选）
@app.route('/')
def index():
    # 获取分类参数，默认显示全部分类
    current_category = request.args.get('category', 'all')
    conn = get_conn()
    cursor = conn.cursor()
    
    if current_category == 'all':
        # 全部分类：显示最新4个商品
        cursor.execute("SELECT * FROM item ORDER BY item_id DESC LIMIT 4")
    else:
        # 指定分类：显示该类别下最新4个商品（参数化查询防注入）
        cursor.execute("""
            SELECT * FROM item 
            WHERE category = %s 
            ORDER BY item_id DESC LIMIT 4
        """, (current_category,))
    
    latest_items = cursor.fetchall()
    conn.close()
    # 把当前分类传给模板，用于高亮显示
    return render_template('index.html', latest_items=latest_items, current_category=current_category)

# 商品列表
# 商品列表（支持分类筛选）
@app.route('/items')
def items():
    # 获取分类参数，默认显示全部分类
    current_category = request.args.get('category', 'all')
    conn = get_conn()
    cursor = conn.cursor()
    
    if current_category == 'all':
        # 全部分类：显示所有商品
        cursor.execute("SELECT * FROM item ORDER BY item_id DESC")
    else:
        # 指定分类：显示该类别下的所有商品
        cursor.execute("""
            SELECT * FROM item 
            WHERE category = %s 
            ORDER BY item_id DESC
        """, (current_category,))
    
    data = cursor.fetchall()
    conn.close()
    # 把当前分类传给模板，用于高亮显示
    return render_template('items.html', data=data, current_category=current_category)

# 用户列表
# 用户列表（支持筛选全部/卖家/买家）
@app.route('/users')
def users():
    user_type = request.args.get('type', 'all')  # 默认显示全部用户
    conn = get_conn()
    cursor = conn.cursor()
    
    if user_type == 'seller':
        # 查询所有发布过商品的卖家（去重）
        cursor.execute("""
            SELECT DISTINCT u.* 
            FROM user u 
            JOIN item i ON u.user_id = i.seller_id
        """)
    elif user_type == 'buyer':
        # 查询所有购买过商品的买家（去重）
        cursor.execute("""
            SELECT DISTINCT u.* 
            FROM user u 
            JOIN orders o ON u.user_id = o.buyer_id
        """)
    else:
        # 查询全部用户
        cursor.execute("SELECT * FROM user")
    
    data = cursor.fetchall()
    conn.close()
    return render_template('users.html', data=data, user_type=user_type)

# 订单列表
# 订单列表（联表查询商品名称）
@app.route('/orders')
def orders():
    conn = get_conn()
    cursor = conn.cursor()
    
    # 1. 联表查询订单（不变）
    cursor.execute("""
        SELECT o.order_id, o.item_id, i.item_name, o.buyer_id, o.order_date
        FROM orders o
        JOIN item i ON o.item_id = i.item_id
        ORDER BY o.order_id DESC
    """)
    data = cursor.fetchall()

    # 2. 重新获取游标，避免和上一个查询冲突
    cursor2 = conn.cursor()
    today = date.today()
    cursor2.execute("""
        SELECT COUNT(*) FROM orders 
        WHERE DATE(order_date) = %s
    """, (today,))
    today_orders = cursor2.fetchone()[0] or 0  # 兜底：如果没结果，强制为0

    conn.close()
    
    # 3. 确保变量名和模板一致
    return render_template('orders.html', data=data, today_orders=today_orders)

# 未售商品
# 未售商品查询
@app.route('/unsold')
def unsold():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM item WHERE status=0")
    data = cursor.fetchall()
    conn.close()
    return render_template('result.html', title="未售商品列表", data=data)

# 已售商品+买家查询
@app.route('/sold')
def sold():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.item_name, u.user_name, o.order_date
        FROM orders o
        JOIN item i ON o.item_id = i.item_id
        JOIN user u ON o.buyer_id = u.user_id
    """)
    data = cursor.fetchall()
    conn.close()
    return render_template('result.html', title="已售商品与买家列表", data=data)



# 删除
@app.route('/delete/<item_id>')
def delete(item_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM item WHERE item_id=%s AND status=0",(item_id,))
    conn.commit()
    conn.close()
    return redirect('/items')

# 购买（自动生成不重复订单号，完美解决重复卡死问题）
# 购买商品（严格匹配作业orders表：order_id、item_id、buyer_id、order_date）
# 购买商品（兼容旧数据：自动识别带o/不带o的订单号，完美解决报错）
# 购买商品（终极完美版：彻底解决订单号重复/字典序问题，兼容所有旧数据）
@app.route('/buy/<item_id>')
def buy(item_id):
    # 未登录则跳转到登录页
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_conn()
    cursor = conn.cursor()

    # 生成订单号（你原来的完美版代码不变）
    cursor.execute("SELECT order_id FROM orders")
    all_orders = cursor.fetchall()
    
    max_num = 0
    for order in all_orders:
        order_id = order[0]
        if order_id.startswith('o'):
            num_str = order_id[1:]
        else:
            num_str = order_id
        
        if num_str.isdigit():
            current_num = int(num_str)
            if current_num > max_num:
                max_num = current_num
    
    new_num = max_num + 1
    order_id = f"o{new_num:03d}"

    # 【核心修改】buyer_id从session获取当前登录用户
    cursor.execute("""
        INSERT INTO orders (order_id, item_id, buyer_id, order_date)
        VALUES (%s, %s, %s, CURDATE())
    """, (order_id, item_id, session['user_id']))

    cursor.execute("UPDATE item SET status = 1 WHERE item_id = %s", (item_id,))

    conn.commit()
    conn.close()
    return redirect('/orders')

# 发布商品（GET显示表单，POST处理提交）
# 发布商品（登录保护+自动填充卖家ID）
# 发布商品（登录保护+图片上传功能）
@app.route('/add_item', methods=['GET', 'POST'])
def add_item():
    # 未登录则跳转到登录页
    if 'user_id' not in session:
        return redirect('/login')
    
    if request.method == 'POST':
        # 获取表单数据
        item_id = request.form['item_id']
        item_name = request.form['item_name']
        category = request.form['category']
        price = request.form['price']
        seller_id = session['user_id']
        
        # 【核心新增】处理图片上传
        image_filename = 'default.jpg'  # 默认图片
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                # 获取文件扩展名
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
                # 用商品ID作为文件名，避免重名
                image_filename = f"{item_id}.{ext}"
                # 保存文件
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
        
        conn = get_conn()
        cursor = conn.cursor()
        # 【修改】INSERT语句添加image字段
        cursor.execute("""
            INSERT INTO item (item_id, item_name, category, price, status, seller_id, image)
            VALUES (%s, %s, %s, %s, 0, %s, %s)
        """, (item_id, item_name, category, price, seller_id, image_filename))
        
        conn.commit()
        conn.close()
        return redirect('/items')
    
    # GET请求显示添加商品表单
    return render_template('add_item.html')

# 用户登录
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_name = request.form['user_name']
        password = request.form['password']
        
        conn = get_conn()
        cursor = conn.cursor()
        # 验证用户名和密码（假设你的user表有password字段）
        cursor.execute("SELECT user_id, user_name FROM user WHERE user_name = %s AND password = %s", (user_name, password))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            # 登录成功，把用户信息存入session
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            return redirect('/')
        else:
            return "用户名或密码错误，<a href='/login'>返回登录</a>"
    
    # GET请求显示登录页面
    return render_template('login.html')

# 用户退出
@app.route('/logout')
def logout():
    # 清除session
    session.clear()
    return redirect('/')




# 全局商品搜索
@app.route('/search')
def search():
    # 获取搜索关键词
    keyword = request.args.get('keyword', '')
    conn = get_conn()
    cursor = conn.cursor()
    
    if keyword:
        # 模糊查询：商品名包含关键词的所有商品（防SQL注入）
        cursor.execute("SELECT * FROM item WHERE item_name LIKE %s", (f'%{keyword}%',))
    else:
        # 空搜索显示全部商品
        cursor.execute("SELECT * FROM item")
    
    data = cursor.fetchall()
    conn.close()
    # 复用items.html模板显示搜索结果
    return render_template('items.html', data=data, keyword=keyword)

# 我的订单（只显示当前登录用户的订单）
@app.route('/my_orders')
def my_orders():
    # 未登录则跳转到登录页
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_conn()
    cursor = conn.cursor()
    # 只查询当前登录用户作为买家的订单，联表获取商品名称
    cursor.execute("""
        SELECT o.order_id, o.item_id, i.item_name, o.buyer_id, o.order_date
        FROM orders o
        JOIN item i ON o.item_id = i.item_id
        WHERE o.buyer_id = %s
        ORDER BY o.order_id DESC
    """, (session['user_id'],))
    
    data = cursor.fetchall()
    conn.close()
    # 直接复用orders.html模板显示个人订单
    return render_template('orders.html', data=data, title="我的订单")
if __name__ == '__main__':
    app.run(debug=True)