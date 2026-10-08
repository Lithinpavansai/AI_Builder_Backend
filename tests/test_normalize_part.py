import unittest
from app.utils.llm import normalize_part
from app.validators.models import AuthSchema, DatabaseSchema, BusinessLogicSchema


class TestNormalizePart(unittest.TestCase):
    def test_list_shape_drops_ui_api_and_extracts_correct_keys(self):
        """Test (a): list shape merges dicts, parses key-value pairs, drops ui/api, and extracts database/auth/business_logic."""
        sample_list_response = [
            {
                "ui": {"pages": [{"name": "Dashboard", "route": "/dashboard", "components": []}]},
                "api": {"endpoints": []},
                "database": {
                    "tables": [
                        {
                            "name": "users",
                            "columns": [{"name": "id", "type": "integer", "primary_key": True}],
                            "relations": []
                        }
                    ]
                }
            },
            "auth",
            ":",
            {
                "roles": ["Admin", "Customer"],
                "permissions": {"Admin": ["all"], "Customer": ["read"]},
                "auth_method": "jwt",
                "token_expiry": "24h",
                "refresh_token": True
            },
            "business_logic",
            ":",
            {
                "rules": [
                    {
                        "name": "RequireAuth",
                        "description": "User must be authenticated",
                        "condition": "not authenticated",
                        "affected_routes": ["/dashboard"],
                        "action": "redirect"
                    }
                ]
            }
        ]

        normalized = normalize_part(sample_list_response, "B")

        # Assert dropped keys
        self.assertNotIn("ui", normalized)
        self.assertNotIn("api", normalized)

        # Assert kept keys
        self.assertIn("database", normalized)
        self.assertIn("auth", normalized)
        self.assertIn("business_logic", normalized)

        # Assert types
        self.assertIsInstance(normalized["database"], dict)
        self.assertIsInstance(normalized["auth"], dict)
        self.assertIsInstance(normalized["business_logic"], dict)

        # Assert auth content has roles and permissions
        self.assertEqual(normalized["auth"]["roles"], ["Admin", "Customer"])
        self.assertEqual(normalized["auth"]["permissions"], {"Admin": ["all"], "Customer": ["read"]})

    def test_plain_dict_unchanged(self):
        """Test (b): plain dict with the right keys remains unchanged."""
        sample_dict = {
            "database": {
                "tables": [
                    {
                        "name": "products",
                        "columns": [{"name": "id", "type": "integer", "primary_key": True}],
                        "relations": []
                    }
                ]
            },
            "auth": {
                "roles": ["Admin"],
                "permissions": {"Admin": ["all"]}
            },
            "business_logic": {
                "rules": []
            }
        }

        normalized = normalize_part(sample_dict, "B")
        self.assertEqual(normalized, sample_dict)

    def test_list_with_missing_auth_raises_readable_error(self):
        """Test (c): list with auth missing raises readable error naming found and missing keys."""
        sample_list_missing_auth = [
            {
                "database": {
                    "tables": []
                }
            },
            "business_logic",
            ":",
            {
                "rules": []
            }
        ]

        with self.assertRaises(ValueError) as ctx:
            normalize_part(sample_list_missing_auth, "B")

        err_msg = str(ctx.exception)
        self.assertIn("Schema Generation part B returned keys", err_msg)
        self.assertIn("missing", err_msg)
        self.assertIn("auth", err_msg)

    def test_normalized_output_builds_pydantic_models(self):
        """Test (d): normalized output builds existing AuthSchema, DatabaseSchema, BusinessLogicSchema without error."""
        sample_list_response = [
            {
                "ui": {"pages": []},
                "api": {"endpoints": []},
                "database": {
                    "tables": [
                        {
                            "name": "users",
                            "columns": [{"name": "id", "type": "integer", "primary_key": True}],
                            "relations": []
                        }
                    ],
                    "database_type": "postgresql"
                }
            },
            "auth",
            ":",
            {
                "roles": ["Admin", "User"],
                "permissions": {"Admin": ["read", "write"], "User": ["read"]},
                "auth_method": "jwt",
                "token_expiry": "24h",
                "refresh_token": True
            },
            "business_logic",
            ":",
            {
                "rules": [
                    {
                        "name": "AdminOnlyRule",
                        "description": "Admin access rule",
                        "condition": "role == 'Admin'",
                        "affected_routes": ["/admin"],
                        "action": "allow"
                    }
                ]
            }
        ]

        normalized = normalize_part(sample_list_response, "B")

        # Instantiate Pydantic models
        db_model = DatabaseSchema(**normalized["database"])
        auth_model = AuthSchema(**normalized["auth"])
        bl_model = BusinessLogicSchema(**normalized["business_logic"])

        self.assertIsInstance(db_model, DatabaseSchema)
        self.assertEqual(db_model.tables[0].name, "users")
        self.assertIsInstance(auth_model, AuthSchema)
        self.assertEqual(auth_model.roles, ["Admin", "User"])
        self.assertIsInstance(bl_model, BusinessLogicSchema)
        self.assertEqual(len(bl_model.rules), 1)


if __name__ == "__main__":
    unittest.main()
