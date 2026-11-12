//! Integration tests use only the public API, as a downstream user would.

#[test]
fn public_api_names_the_crate() {
    assert_eq!(@IDENT@::name(), "@NAME@");
}
