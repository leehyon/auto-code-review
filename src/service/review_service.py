import pandas as pd

from src.entity.review_entity import MergeRequestReviewEntity, PushReviewEntity
from src.service.db_factory import get_db_connection, DatabaseFactory
from src.utils.log import logger
from src.utils.review_summary import extract_summary


class ReviewService:

    @staticmethod
    def _get_create_table_sql(table_name: str) -> str:
        """获取创建表的SQL语句（兼容MySQL和SQLite）"""
        is_mysql = DatabaseFactory.is_mysql()

        if table_name == "mr_review_log":
            if is_mysql:
                return '''
                    CREATE TABLE IF NOT EXISTS mr_review_log (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        project_name VARCHAR(255),
                        author VARCHAR(255),
                        source_branch VARCHAR(255),
                        target_branch VARCHAR(255),
                        updated_at BIGINT,
                        commit_messages TEXT,
                        score INT,
                        url TEXT,
                        review_result LONGTEXT,
                        additions INT DEFAULT 0,
                        deletions INT DEFAULT 0,
                        status VARCHAR(16) DEFAULT 'success',
                        error_message TEXT,
                        platform VARCHAR(32),
                        webhook_data LONGTEXT,
                        INDEX idx_author (author),
                        INDEX idx_project_name (project_name),
                        INDEX idx_updated_at (updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                '''
            else:
                return '''
                    CREATE TABLE IF NOT EXISTS mr_review_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_name TEXT,
                        author TEXT,
                        source_branch TEXT,
                        target_branch TEXT,
                        updated_at INTEGER,
                        commit_messages TEXT,
                        score INTEGER,
                        url TEXT,
                        review_result TEXT,
                        additions INTEGER DEFAULT 0,
                        deletions INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'success',
                        error_message TEXT,
                        platform TEXT,
                        webhook_data TEXT
                    )
                '''
        elif table_name == "push_review_log":
            if is_mysql:
                return '''
                    CREATE TABLE IF NOT EXISTS push_review_log (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        project_name VARCHAR(255),
                        author VARCHAR(255),
                        branch VARCHAR(255),
                        updated_at BIGINT,
                        commit_messages TEXT,
                        score INT,
                        review_result LONGTEXT,
                        additions INT DEFAULT 0,
                        deletions INT DEFAULT 0,
                        url TEXT,
                        status VARCHAR(16) DEFAULT 'success',
                        error_message TEXT,
                        platform VARCHAR(32),
                        webhook_data LONGTEXT,
                        INDEX idx_author (author),
                        INDEX idx_project_name (project_name),
                        INDEX idx_updated_at (updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                '''
            else:
                return '''
                    CREATE TABLE IF NOT EXISTS push_review_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_name TEXT,
                        author TEXT,
                        branch TEXT,
                        updated_at INTEGER,
                        commit_messages TEXT,
                        score INTEGER,
                        review_result TEXT,
                        additions INTEGER DEFAULT 0,
                        deletions INTEGER DEFAULT 0,
                        url TEXT,
                        status TEXT DEFAULT 'success',
                        error_message TEXT,
                        platform TEXT,
                        webhook_data TEXT
                    )
                '''
        return ""

    @staticmethod
    def init_db():
        """初始化数据库及表结构"""
        try:
            with get_db_connection() as conn:
                # 创建mr_review_log表
                conn.execute(ReviewService._get_create_table_sql("mr_review_log"))

                # 创建push_review_log表
                conn.execute(ReviewService._get_create_table_sql("push_review_log"))

                # 为旧版本的表补齐新增的列。
                # 新建的表已经带上这些列；已存在的表（SQLite 和 MySQL 都可能）需要 ALTER 一次。
                table_columns = {
                    "mr_review_log": [
                        ("additions", "INTEGER DEFAULT 0"),
                        ("deletions", "INTEGER DEFAULT 0"),
                        ("status", "TEXT DEFAULT 'success'"),
                        ("error_message", "TEXT"),
                        ("platform", "TEXT"),
                        ("webhook_data", "TEXT"),
                    ],
                    "push_review_log": [
                        ("additions", "INTEGER DEFAULT 0"),
                        ("deletions", "INTEGER DEFAULT 0"),
                        ("url", "TEXT"),
                        ("status", "TEXT DEFAULT 'success'"),
                        ("error_message", "TEXT"),
                        ("platform", "TEXT"),
                        ("webhook_data", "TEXT"),
                    ],
                }
                for table, columns in table_columns.items():
                    for column, ddl in columns:
                        if not conn.column_exists(table, column):
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

                conn.commit()
                print(f"Database initialized successfully using {DatabaseFactory.get_db_type()}")
        except Exception as e:
            print(f"Database initialization failed: {e}")

    @staticmethod
    def insert_mr_review_log(entity: MergeRequestReviewEntity):
        """插入合并请求审核日志

        status 为 'failed' 时表示审查没跑完（模型超时、网络错误等），
        这类记录没有 review_result，也没有在 PR 上留评论，
        但会出现在 Dashboard 里，供人工点重试。

        review_result 只存 Summary 段：完整报告已经作为评论发到 PR 上了，
        全文入库会让这一列占到记录的 90% 以上。
        """
        try:
            with get_db_connection() as conn:
                conn.execute('''
                    INSERT INTO mr_review_log (project_name, author, source_branch, target_branch,
                        updated_at, commit_messages, score, url, review_result, additions, deletions,
                        status, error_message, platform, webhook_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (entity.project_name, entity.author, entity.source_branch,
                      entity.target_branch, entity.updated_at, entity.commit_messages,
                      entity.score, entity.url, extract_summary(entity.review_result),
                      entity.additions, entity.deletions,
                      getattr(entity, 'status', 'success'),
                      getattr(entity, 'error_message', ''),
                      getattr(entity, 'platform', ''),
                      ReviewService._dump_webhook_data(getattr(entity, 'webhook_data', None))))
                conn.commit()
                print(f"[ReviewService] MR review log inserted successfully: {entity.project_name} by {entity.author}")
        except Exception as e:
            import traceback
            print(f"[ReviewService] Error inserting MR review log: {e}")
            print(f"[ReviewService] Traceback: {traceback.format_exc()}")

    @staticmethod
    def get_mr_review_logs(authors: list = None, project_names: list = None, updated_at_gte: int = None,
                           updated_at_lte: int = None) -> pd.DataFrame:
        """获取符合条件的合并请求审核日志"""
        try:
            with get_db_connection() as conn:
                query = """
                    SELECT id, project_name, author, source_branch, target_branch, updated_at,
                           commit_messages, score, url, additions, deletions,
                           status, error_message, platform
                    FROM mr_review_log
                    WHERE 1=1
                """
                params = []

                ph = conn.placeholder

                if authors:
                    placeholders = ','.join([ph] * len(authors))
                    query += f" AND author IN ({placeholders})"
                    params.extend(authors)

                if project_names:
                    # 项目名统一小写存储，筛选也按小写比较，
                    # 免得前端传了大写就查不到记录
                    placeholders = ','.join([ph] * len(project_names))
                    query += f" AND LOWER(project_name) IN ({placeholders})"
                    params.extend([p.lower() for p in project_names])

                if updated_at_gte is not None:
                    query += f" AND updated_at >= {ph}"
                    params.append(updated_at_gte)

                if updated_at_lte is not None:
                    query += f" AND updated_at <= {ph}"
                    params.append(updated_at_lte)

                query += " ORDER BY updated_at DESC"

                # 使用pandas读取数据
                pandas_conn = conn.get_connection_for_pandas()
                df = pd.read_sql_query(sql=query, con=pandas_conn, params=params)
                return df
        except Exception as e:
            logger.error(f"Error retrieving review logs: {e}", exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def insert_push_review_log(entity: PushReviewEntity):
        """插入推送审核日志

        review_result 同样只存 Summary 段，理由同 insert_mr_review_log。
        """
        try:
            with get_db_connection() as conn:
                conn.execute('''
                    INSERT INTO push_review_log (project_name, author, branch, updated_at,
                        commit_messages, score, review_result, additions, deletions, url,
                        status, error_message, platform, webhook_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (entity.project_name, entity.author, entity.branch,
                      entity.updated_at, entity.commit_messages, entity.score,
                      extract_summary(entity.review_result),
                      entity.additions, entity.deletions,
                      entity.url,
                      getattr(entity, 'status', 'success'),
                      getattr(entity, 'error_message', ''),
                      getattr(entity, 'platform', ''),
                      ReviewService._dump_webhook_data(getattr(entity, 'webhook_data', None))))
                conn.commit()
                print(f"[ReviewService] Push review log inserted successfully: {entity.project_name} by {entity.author}")
        except Exception as e:
            import traceback
            print(f"[ReviewService] Error inserting push review log: {e}")
            print(f"[ReviewService] Traceback: {traceback.format_exc()}")

    @staticmethod
    def _dump_webhook_data(webhook_data) -> str:
        """把 webhook payload 序列化成 JSON 存库。

        重试时要用它还原触发现场，所以必须存；序列化失败就存空串，
        不能因为存不下而让审查记录丢失。
        """
        if not webhook_data:
            return ''
        try:
            import json
            return json.dumps(webhook_data, ensure_ascii=False, default=str)
        except Exception:
            return ''

    @staticmethod
    def _load_webhook_data(raw) -> dict:
        """把库里的 webhook payload 还原成 dict。"""
        if not raw:
            return {}
        try:
            import json
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def update_review_log(review_type: str, record_id: int, status: str,
                          error_message: str = '', score=None,
                          review_result: str = None, additions=None,
                          deletions=None) -> bool:
        """更新一条审查记录的状态。重试成功后把失败记录改成成功。"""
        table = 'push_review_log' if review_type == 'push' else 'mr_review_log'
        try:
            with get_db_connection() as conn:
                sets = ['status = ?', 'error_message = ?']
                params = [status, error_message or '']
                if score is not None:
                    sets.append('score = ?')
                    params.append(score)
                if review_result is not None:
                    sets.append('review_result = ?')
                    params.append(review_result)
                if additions is not None:
                    sets.append('additions = ?')
                    params.append(additions)
                if deletions is not None:
                    sets.append('deletions = ?')
                    params.append(deletions)
                params.append(record_id)
                conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", tuple(params))
                conn.commit()
                return True
        except Exception as e:
            import traceback
            print(f"[ReviewService] Error updating review log: {e}")
            print(f"[ReviewService] Traceback: {traceback.format_exc()}")
            return False

    @staticmethod
    def get_newest_review_log_id(review_type: str):
        """取最新的记录 id。

        重试时用来判断「重放有没有产出新记录」：新写入的记录 id 一定比旧的大。
        """
        table = 'push_review_log' if review_type == 'push' else 'mr_review_log'
        try:
            with get_db_connection() as conn:
                row = conn.fetchone(f"SELECT MAX(id) FROM {table}")
                return row[0] if row and row[0] is not None else None
        except Exception as e:
            print(f"[ReviewService] Error getting newest review log id: {e}")
            return None

    @staticmethod
    def delete_review_log(review_type: str, record_id: int) -> bool:
        """删除一条审查记录。

        只在重放确实产出了新记录之后调用 —— 新记录顶上后删掉旧的失败行，
        Dashboard 上不会出现重复记录。
        """
        table = 'push_review_log' if review_type == 'push' else 'mr_review_log'
        try:
            with get_db_connection() as conn:
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
                conn.commit()
                return True
        except Exception as e:
            import traceback
            print(f"[ReviewService] Error deleting review log: {e}")
            print(f"[ReviewService] Traceback: {traceback.format_exc()}")
            return False

    @staticmethod
    def get_review_log(review_type: str, record_id: int):
        """按 id 取一条审查记录（含 webhook payload），用于重试。"""
        table = 'push_review_log' if review_type == 'push' else 'mr_review_log'
        # 两张表的列不一样：MR 有源/目标分支，Push 只有分支
        if review_type == 'push':
            columns = ['id', 'project_name', 'author', 'branch', 'updated_at',
                       'commit_messages', 'score', 'url', 'review_result', 'additions',
                       'deletions', 'status', 'error_message', 'platform', 'webhook_data']
        else:
            columns = ['id', 'project_name', 'author', 'source_branch', 'target_branch',
                       'updated_at', 'commit_messages', 'score', 'url', 'review_result',
                       'additions', 'deletions', 'status', 'error_message', 'platform',
                       'webhook_data']
        try:
            with get_db_connection() as conn:
                row = conn.fetchone(
                    f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?", (record_id,))
                if not row:
                    return None
                record = dict(zip(columns, row))
                record['webhook_data'] = ReviewService._load_webhook_data(record['webhook_data'])
                return record
        except Exception as e:
            import traceback
            print(f"[ReviewService] Error fetching review log: {e}")
            print(f"[ReviewService] Traceback: {traceback.format_exc()}")
            return None

    @staticmethod
    def get_push_review_logs(authors: list = None, project_names: list = None, updated_at_gte: int = None,
                             updated_at_lte: int = None) -> pd.DataFrame:
        """获取符合条件的推送审核日志"""
        try:
            with get_db_connection() as conn:
                query = """
                    SELECT id, project_name, author, branch, updated_at, commit_messages,
                           score, additions, deletions, url,
                           status, error_message, platform
                    FROM push_review_log
                    WHERE 1=1
                """
                params = []

                ph = conn.placeholder

                if authors:
                    placeholders = ','.join([ph] * len(authors))
                    query += f" AND author IN ({placeholders})"
                    params.extend(authors)

                if project_names:
                    # 项目名统一小写存储，筛选也按小写比较，
                    # 免得前端传了大写就查不到记录
                    placeholders = ','.join([ph] * len(project_names))
                    query += f" AND LOWER(project_name) IN ({placeholders})"
                    params.extend([p.lower() for p in project_names])

                if updated_at_gte is not None:
                    query += f" AND updated_at >= {ph}"
                    params.append(updated_at_gte)

                if updated_at_lte is not None:
                    query += f" AND updated_at <= {ph}"
                    params.append(updated_at_lte)

                query += " ORDER BY updated_at DESC"

                # 使用pandas读取数据
                pandas_conn = conn.get_connection_for_pandas()
                df = pd.read_sql_query(sql=query, con=pandas_conn, params=params)
                return df
        except Exception as e:
            logger.error(f"Error retrieving push review logs: {e}", exc_info=True)
            return pd.DataFrame()


# Initialize database
ReviewService.init_db()
