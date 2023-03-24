import redis

from onestep.middleware import RedisConfigMiddleware

rds_params = {
    'host': 'r-gc7066ooe477c8h72epd.redis.cn-chengdu.rds.aliyuncs.com',
    'port': '6379',
    'password': 'qweQWE123!@#',
    'max_connections': 10,
    'db': 10,
}
rds_client = redis.Redis(**rds_params)
