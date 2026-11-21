-- PostgreSQL stored functions for Lab 6 (FIND_TM / MR_Q / SUM_MR / TR_TO / ROUTING_COST).
-- Run after lab6_migration.sql.
CREATE OR REPLACE FUNCTION "FIND_TM"(p_route_id integer, p_quantity numeric DEFAULT 1)
RETURNS TABLE (node_id integer, kind text, route_id integer, required_quantity numeric,
               operation_name text, product_id integer)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_final integer; v_has_cycle boolean; v_unreachable integer;
BEGIN
 IF p_quantity IS NULL OR p_quantity<=0 THEN RAISE EXCEPTION 'INVALID_QUANTITY: количество должно быть больше нуля'; END IF;
 SELECT r.final_node_id INTO v_final FROM routing_routes r WHERE r.id=p_route_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'ROUTE_NOT_FOUND: маршрут %',p_route_id; END IF;
 IF v_final IS NULL OR NOT EXISTS(SELECT 1 FROM routing_nodes n WHERE n.id=v_final AND n.route_id=p_route_id AND n.kind='production')
 THEN RAISE EXCEPTION 'INCOMPLETE_ROUTE: итоговая операция не установлена'; END IF;
 WITH RECURSIVE graph(id,qty,path,cycle) AS (
    SELECT v_final,p_quantity::numeric,ARRAY[v_final],false
    UNION ALL
    SELECT i.input_node_id, g.qty*i.quantity/i.for_quantity,
           g.path||i.input_node_id, i.input_node_id=ANY(g.path)
    FROM graph g JOIN routing_inputs i ON i.output_node_id=g.id WHERE NOT g.cycle
 ) SELECT bool_or(cycle) INTO v_has_cycle FROM graph;
 IF v_has_cycle THEN RAISE EXCEPTION 'CYCLE_DETECTED: цикл производственных операций'; END IF;
 WITH RECURSIVE graph(id,path) AS (
   SELECT v_final,ARRAY[v_final]
   UNION ALL SELECT i.input_node_id,g.path||i.input_node_id
   FROM graph g JOIN routing_inputs i ON i.output_node_id=g.id
   WHERE NOT i.input_node_id=ANY(g.path)
 ) SELECT count(*) INTO v_unreachable FROM routing_nodes n
   WHERE n.kind='production' AND n.route_id=p_route_id
     AND NOT EXISTS(SELECT 1 FROM graph g WHERE g.id=n.id);
 IF v_unreachable>0 THEN RAISE EXCEPTION 'INCOMPLETE_ROUTE: не все операции достижимы от итога (% шт.)',v_unreachable; END IF;
 RETURN QUERY
 WITH RECURSIVE graph(id,qty,path) AS (
    SELECT v_final,p_quantity::numeric,ARRAY[v_final]
    UNION ALL
    SELECT i.input_node_id,g.qty*i.quantity/i.for_quantity,g.path||i.input_node_id
    FROM graph g JOIN routing_inputs i ON i.output_node_id=g.id
    WHERE NOT i.input_node_id=ANY(g.path)
 ) SELECT n.id,n.kind::text,n.route_id,SUM(g.qty)::numeric,n.operation_name,n.purchase_product_id
 FROM graph g JOIN routing_nodes n ON n.id=g.id
 GROUP BY n.id,n.kind,n.route_id,n.operation_name,n.purchase_product_id ORDER BY n.id;
END $$;

CREATE OR REPLACE FUNCTION "MR_Q"(p_operation_id integer, p_quantity numeric DEFAULT 1)
RETURNS TABLE(source_id integer, kind text, product_id integer, quantity numeric, for_quantity numeric)
LANGUAGE plpgsql STABLE AS $$
BEGIN
 IF p_quantity IS NULL OR p_quantity<=0 THEN RAISE EXCEPTION 'INVALID_QUANTITY'; END IF;
 IF NOT EXISTS(SELECT 1 FROM routing_nodes n WHERE n.id=p_operation_id AND n.kind='production') THEN
 RAISE EXCEPTION 'OPERATION_NOT_FOUND: %',p_operation_id; END IF;
 RETURN QUERY SELECT n.id,n.kind::text,n.purchase_product_id,(i.quantity*p_quantity/i.for_quantity)::numeric,i.for_quantity
 FROM routing_inputs i JOIN routing_nodes n ON n.id=i.input_node_id
 WHERE i.output_node_id=p_operation_id ORDER BY n.id;
END $$;

CREATE OR REPLACE FUNCTION "SUM_MR"(p_route_id integer, p_quantity numeric DEFAULT 1)
RETURNS TABLE(product_id integer, quantity numeric)
LANGUAGE sql STABLE AS $$
 SELECT t.product_id,SUM(t.required_quantity)::numeric
 FROM "FIND_TM"(p_route_id,p_quantity) t WHERE t.kind='purchase'
 GROUP BY t.product_id ORDER BY t.product_id;
$$;

CREATE OR REPLACE FUNCTION "TR_TO"(p_route_id integer, p_quantity numeric DEFAULT 1)
RETURNS TABLE(operation_id integer, operation_name text, route_id integer, work_center_id integer,
 profession_id integer, qualification_id integer, setup_minutes numeric,
 run_minutes numeric,total_minutes numeric,required_quantity numeric)
LANGUAGE sql STABLE AS $$
 SELECT n.id,n.operation_name,n.route_id,n.work_center_id,n.profession_id,n.qualification_id,
        n.setup_minutes::numeric,(n.unit_minutes*t.required_quantity)::numeric,
        (n.setup_minutes+n.unit_minutes*t.required_quantity)::numeric,t.required_quantity
 FROM "FIND_TM"(p_route_id,p_quantity) t JOIN routing_nodes n ON n.id=t.node_id
 WHERE t.kind='production' ORDER BY n.route_id,n.number,n.id;
$$;

CREATE OR REPLACE FUNCTION "ROUTING_COST"(p_route_id integer, p_quantity numeric DEFAULT 1)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE v_missing text; v_mat numeric; v_labor numeric;
BEGIN
 SELECT p.name INTO v_missing FROM "SUM_MR"(p_route_id,p_quantity) s
 JOIN products p ON p.id_product=s.product_id
 LEFT JOIN routing_material_prices pr ON pr.product_id=s.product_id
 WHERE pr.product_id IS NULL LIMIT 1;
 IF v_missing IS NOT NULL THEN RAISE EXCEPTION 'MISSING_MATERIAL_PRICE: %',v_missing; END IF;
 SELECT SUM(s.quantity*pr.unit_cost) INTO v_mat FROM "SUM_MR"(p_route_id,p_quantity) s
 JOIN routing_material_prices pr ON pr.product_id=s.product_id;
 SELECT SUM(t.total_minutes/60 * pr.base_hourly_rate * q.coefficient) INTO v_labor
 FROM "TR_TO"(p_route_id,p_quantity) t
 JOIN routing_professions pr ON pr.id=t.profession_id
 JOIN routing_qualifications q ON q.id=t.qualification_id;
 RETURN jsonb_build_object('quantity',p_quantity,'currency','RUB','pricing','current',
    'material_cost',ROUND(COALESCE(v_mat,0),2),
    'labor_cost',ROUND(COALESCE(v_labor,0),2),
    'total_cost',ROUND(COALESCE(v_mat,0)+COALESCE(v_labor,0),2));
END $$;
