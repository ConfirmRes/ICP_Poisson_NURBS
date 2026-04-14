-- 航发叶片检测平台 — MySQL 8.x
-- 在 Navicat 中：新建查询 → 粘贴执行；或命令行 mysql -u root -p < init.sql

CREATE DATABASE IF NOT EXISTS blade_inspection
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE blade_inspection;

-- 叶片记录：手动录入元数据 + 流水线状态 + 指标（与前端指标管理联动）
CREATE TABLE IF NOT EXISTS blade_record (
  id              VARCHAR(64)  NOT NULL COMMENT '记录ID（可手动指定）',
  blade_type      VARCHAR(64)  NOT NULL COMMENT '叶片类型',
  blade_no        VARCHAR(64)  NOT NULL COMMENT '叶片编号',
  inspect_date    DATE         NOT NULL COMMENT '检测日期',
  inspector       VARCHAR(128) NOT NULL COMMENT '送检人员',
  batch_no        VARCHAR(64)  NOT NULL COMMENT '检测批次',
  length_mm       DOUBLE       NULL COMMENT '叶片长度 mm',
  chord_mm        DOUBLE       NULL COMMENT '弦长 mm',
  twist_deg       DOUBLE       NULL COMMENT '扭转角 °',
  thick_mm        DOUBLE       NULL COMMENT '最大厚度 mm',
  eval_status     VARCHAR(32)  NOT NULL DEFAULT '待评估' COMMENT '评估：合格/待评估/不合格/需优化',
  icp_status      VARCHAR(32)  NOT NULL DEFAULT 'pending',
  ps_status       VARCHAR(32)  NOT NULL DEFAULT 'pending',
  nb_status       VARCHAR(32)  NOT NULL DEFAULT 'pending',
  icp_rmse        DOUBLE       NULL,
  icp_iterations  INT          NULL,
  transform_json  JSON         NULL COMMENT '4x4 刚体变换（行优先）',
  registered_pcd_rel VARCHAR(512) NULL COMMENT '配准后点云相对路径',
  mesh_stl_rel    VARCHAR(512) NULL COMMENT '泊松水密网格 STL 相对路径',
  error_message   VARCHAR(1024) NULL COMMENT '流水线错误信息',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_blade_type (blade_type),
  KEY idx_inspect_date (inspect_date),
  KEY idx_inspector (inspector),
  KEY idx_eval (eval_status),
  KEY idx_batch (batch_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 可选：示例数据（不需要可删）
INSERT INTO blade_record (
  id, blade_type, blade_no, inspect_date, inspector, batch_no,
  length_mm, chord_mm, twist_deg, thick_mm, eval_status,
  icp_status, ps_status, nb_status
) VALUES
(
  'DEMO-001', '压气机叶片', 'BL-1001', '2026-04-01', '张三', 'BATCH-2026-01',
  120.5, 78.2, 28.3, 4.2, '合格',
  'completed', 'completed', 'completed'
)
ON DUPLICATE KEY UPDATE id = id;
