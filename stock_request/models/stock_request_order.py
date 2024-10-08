# Copyright 2018 Creu Blanca
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StockRequestOrder(models.Model):
    _name = "stock.request.order"
    _description = "Stock Request Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        warehouse = None
        if "warehouse_id" not in res and res.get("company_id"):
            warehouse = self.env["stock.warehouse"].search(
                [("company_id", "=", res["company_id"])], limit=1
            )
        if warehouse:
            res["warehouse_id"] = warehouse.id
            res["location_id"] = warehouse.lot_stock_id.id
        return res

    def __get_request_order_states(self):
        return self.env["stock.request"].fields_get(allfields=["state"])["state"][
            "selection"
        ]

    def _get_request_order_states(self):
        return self.__get_request_order_states()

    def _get_default_requested_by(self):
        return self.env["res.users"].browse(self.env.uid)

    name = fields.Char(
        copy=False,
        required=True,
        readonly=True,
        default="/",
    )
    state = fields.Selection(
        selection=_get_request_order_states,
        string="Status",
        copy=False,
        default="draft",
        index=True,
        readonly=True,
        tracking=True,
        compute="_compute_state",
        store=True,
    )
    requested_by = fields.Many2one(
        "res.users",
        required=True,
        tracking=True,
        default=lambda s: s._get_default_requested_by(),
    )
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        check_company=True,
        ondelete="cascade",
        required=True,
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Location",
        domain="not allow_virtual_location and "
        "[('usage', 'in', ['internal', 'transit']), ('usage', '!=', 'production')] or []",
        ondelete="cascade",
        required=True,
        )
    allow_virtual_location = fields.Boolean(
        related="company_id.stock_request_allow_virtual_loc", readonly=True
    )
    procurement_group_id = fields.Many2one(
        "procurement.group",
        "Procurement Group",
        help="Moves created through this stock request will be put in this "
        "procurement group. If none is given, the moves generated by "
        "procurement rules will be grouped into one big picking.",
    )
    company_id = fields.Many2one(
        "res.company",
        "Company",
        required=True,
        default=lambda self: self.env.company,
    )
    expected_date = fields.Datetime(
        default=fields.Datetime.now,
        index=True,
        required=True,
        help="Date when you expect to receive the goods.",
    )
    picking_policy = fields.Selection(
        [
            ("direct", "Receive each product when available"),
            ("one", "Receive all products at once"),
        ],
        string="Shipping Policy",
        required=True,
        default="direct",
    )
    move_ids = fields.One2many(
        comodel_name="stock.move",
        compute="_compute_move_ids",
        string="Stock Moves",
        readonly=True,
    )
    picking_ids = fields.One2many(
        "stock.picking",
        compute="_compute_picking_ids",
        string="Pickings",
        readonly=True,
    )
    picking_count = fields.Integer(
        string="Delivery Orders", compute="_compute_picking_ids", readonly=True
    )
    stock_request_ids = fields.One2many(
        "stock.request", inverse_name="order_id", copy=True
    )
    stock_request_count = fields.Integer(
        string="Stock requests", compute="_compute_stock_request_count", readonly=True
    )

    route_ids = fields.Many2many(
        "stock.route",
        string="Routes",
        compute="_compute_route_ids",
        readonly=True,
        store=True,
    )

    route_id = fields.Many2one(
        "stock.route",
        compute="_compute_route_id",
        inverse="_inverse_route_id",
        readonly=True,
        store=True,
        help="The route related to a stock request order",
    )

    _sql_constraints = [
        ("name_uniq", "unique(name, company_id)", "Stock Request name must be unique")
    ]

    @api.depends('warehouse_id')
    def _compute_route_ids(self):
        for record in self:
            if record.warehouse_id:
                # Reemplazamos 'stock.location.route' por 'stock.route'
                routes = self.env['stock.route'].sudo().search([
                    ('warehouse_ids', 'in', record.warehouse_id.id)
                ])
                record.route_ids = routes


    def get_parents(self):
        location = self.location_id
        result = location
        while location.location_id:
            location = location.location_id
            result |= location
        return result

    @api.depends("stock_request_ids")
    def _compute_route_id(self):
        for order in self:
            if order.stock_request_ids:
                first_route = order.stock_request_ids[0].route_id or False
                if any(r.route_id != first_route for r in order.stock_request_ids):
                    first_route = False
                order.route_id = first_route

    def _inverse_route_id(self):
        for order in self:
            if order.route_id:
                order.stock_request_ids.write({"route_id": order.route_id.id})

    @api.onchange("route_id")
    def _onchange_route_id(self):
        if self.route_id:
            for request in self.stock_request_ids:
                request.route_id = self.route_id

    @api.depends("stock_request_ids.state")
    def _compute_state(self):
        for item in self:
            states = item.stock_request_ids.mapped("state")
            if not item.stock_request_ids or all(x == "draft" for x in states):
                item.state = "draft"
            elif all(x == "cancel" for x in states):
                item.state = "cancel"
            elif all(x in ("done", "cancel") for x in states):
                item.state = "done"
            else:
                item.state = "open"

    @api.depends("stock_request_ids.allocation_ids")
    def _compute_picking_ids(self):
        for record in self:
            record.picking_ids = record.stock_request_ids.mapped("picking_ids")
            record.picking_count = len(record.picking_ids)

    @api.depends("stock_request_ids")
    def _compute_move_ids(self):
        for record in self:
            record.move_ids = record.stock_request_ids.mapped("move_ids")

    @api.depends("stock_request_ids")
    def _compute_stock_request_count(self):
        for record in self:
            record.stock_request_count = len(record.stock_request_ids)

    @api.onchange("requested_by")
    def onchange_requested_by(self):
        self.change_childs()

    @api.onchange("expected_date")
    def onchange_expected_date(self):
        self.change_childs()

    @api.onchange("picking_policy")
    def onchange_picking_policy(self):
        self.change_childs()


    # @api.onchange('warehouse_id')
    # def _onchange_warehouse_id(self):
    #     if self.warehouse_id:
    #         location_root_id = self.warehouse_id.view_location_id.id
    #         print(f"Ubicación raíz de la bodega: {location_root_id}")  # Depuración
    #         if location_root_id:
    #             return {
    #                 'domain': {
    #                     'location_id': [
    #                         ('id', 'child_of', location_root_id),
    #                         ('usage', 'in', ['internal', 'transit'])
    #                     ]
    #                 }
    #             }
        

    # @api.onchange('location_id')
    # def onchange_location_id(self):
    #      if self.location_id:
    #          # Asignar la ubicación a todas las líneas de productos
    #          for line in self.stock_request_ids:
    #              line.location_id = self.location_id
    #      self.change_childs()


    @api.onchange("procurement_group_id")
    def onchange_procurement_group_id(self):
        self.change_childs()

    @api.onchange("company_id")
    def onchange_company_id(self):
        if self.company_id and (
            not self.warehouse_id or self.warehouse_id.company_id != self.company_id
        ):
            self.warehouse_id = self.env["stock.warehouse"].search(
                [("company_id", "=", self.company_id.id)], limit=1
            )
            self.with_context(no_change_childs=True).onchange_warehouse_id()
        self.change_childs()

    def change_childs(self):
        if not self._context.get("no_change_childs", False):
            for line in self.stock_request_ids:
                line.warehouse_id = self.warehouse_id
                line.location_id = self.location_id
                line.company_id = self.company_id
                line.picking_policy = self.picking_policy
                line.expected_date = self.expected_date
                line.requested_by = self.requested_by
                line.procurement_group_id = self.procurement_group_id
                # Agregar propagación de ruta y unidad de medida
                if self.route_ids:
                    line.route_id = self.route_ids[0]
         #      if self.uom_id:
        #         line.product_uom_id = self.uom_id


    def action_confirm(self):
        if not self.stock_request_ids:
            raise UserError(
                _("There should be at least one request item for confirming the order.")
            )
        self.mapped("stock_request_ids").action_confirm()
        return True

    def action_draft(self):
        self.mapped("stock_request_ids").action_draft()
        return True

    def action_cancel(self):
        self.mapped("stock_request_ids").action_cancel()
        return True

    def action_done(self):
        return True

    def action_view_transfer(self):
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )

        pickings = self.mapped("picking_ids")
        if len(pickings) > 1:
            action["domain"] = [("id", "in", pickings.ids)]
        elif pickings:
            action["views"] = [(self.env.ref("stock.view_picking_form").id, "form")]
            action["res_id"] = pickings.id
        return action

    def action_view_stock_requests(self):
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.action_stock_request_form"
        )
        if len(self.stock_request_ids) > 1:
            action["domain"] = [("order_id", "in", self.ids)]
        elif self.stock_request_ids:
            action["views"] = [
                (self.env.ref("stock_request.view_stock_request_form").id, "form")
            ]
            action["res_id"] = self.stock_request_ids.id
        return action

    @api.model_create_multi
    def create(self, vals_list):
        vals_list_upd = []
        for vals in vals_list:
            upd_vals = vals.copy()
            if upd_vals.get("name", "/") == "/":
                upd_vals["name"] = self.env["ir.sequence"].next_by_code(
                    "stock.request.order"
                )
            vals_list_upd.append(upd_vals)
        return super().create(vals_list_upd)

    def unlink(self):
        if self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("Only orders on draft state can be unlinked"))
        return super().unlink()

    @api.constrains("warehouse_id", "company_id")
    def _check_warehouse_company(self):
        if any(
            request.warehouse_id.company_id != request.company_id for request in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with "
                    "that of the warehouse."
                )
            )

    @api.constrains("location_id", "company_id")
    def _check_location_company(self):
        if any(
            request.location_id.company_id
            and request.location_id.company_id != request.company_id
            for request in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with "
                    "that of the location."
                )
            )

    @api.model
    def _create_from_product_multiselect(self, products):
        if not products:
            return False
        if products._name not in ("product.product", "product.template"):
            raise ValidationError(
                _("This action only works in the context of products")
            )
        if products._name == "product.template":
            # search instead of mapped so we don't include archived variants
            products = self.env["product.product"].search(
                [("product_tmpl_id", "in", products.ids)]
            )
        expected = self.default_get(["expected_date"])["expected_date"]
        order = self.env["stock.request.order"].create(
            dict(
                expected_date=expected,
                stock_request_ids=[
                    (
                        0,
                        0,
                        dict(
                            product_id=product.id,
                            product_uom_id=product.uom_id.id,
                            product_uom_qty=1.0,
                            expected_date=expected,
                        ),
                    )
                    for product in products
                ],
            )
        )
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_order_action"
        )
        action["views"] = [
            (self.env.ref("stock_request.stock_request_order_form").id, "form")
        ]
        action["res_id"] = order.id
        return action
    

    @api.depends('product_id', 'route_id')
    def _compute_available_qty(self):
        for line in self:
            if line.product_id and line.route_id:
                stock_qty = self.env['stock.quant'].sudo().search([
                    ('product_id', '=', line.product_id.id),
                    ('location_id', 'in', line.route_id.mapped('location_ids').ids)
                ]).quantity
                line.available_qty = stock_qty
            else:
                line.available_qty = 0.0

    # #funcion imprimir reporte 
    # def action_print_report(self):
    #     return self.env.ref('stock_request_order.action_report_stock_request_order').report_action(self)
